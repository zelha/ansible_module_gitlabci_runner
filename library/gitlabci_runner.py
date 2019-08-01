#!/usr/bin/env python
#-*- coding: utf-8 -*-

from ansible.module_utils.basic import AnsibleModule
from sys import stderr
from subprocess import Popen, call, PIPE, STDOUT, check_output, CalledProcessError
from distutils import spawn
import sys, shlex, uuid, re, filecmp, os, subprocess

#define the available arguments/parameters that user can pass to the module
# command can be register|unregister
# executor can be docker|ssh|shell
module_args = dict(
    command                 = dict(required=True,   type='str'), 
    executor                = dict(required=True,   type='str'),
    listen_address          = dict(required=False,  type='str', default="0.0.0.0:9100")
)
#@todo: add support for docker options 
#--limit "0",--output-limit "0",--request-concurrency "0",--tls-ca-file,--tls-cert-file,--tls-key-file,--builds-dir
#--cache-dir,--builds-dir,--cache-dir,--docker-host,--docker-cert-path,--docker-tlsverify,--docker-hostname 
#--docker-cpuset-cpus,--docker-cpus,--docker-dns,--docker-dns-search,--docker-userns,--docker-cap-add,--docker-cap-drop                 
#--docker-security-opt,--docker-devices,--docker-disable-cache,--docker-volumes,--docker-volume-driver,--docker-cache-dir                 
#--docker-extra-hosts,--docker-volumes-from,--docker-network-mode,--docker-links,--docker-services,--docker-wait-for-services-timeout "0"
#--docker-allowed-images,--docker-allowed-services,--docker-shm-size "0",--docker-tmpfs "{}",--docker-services-tmpfs "{}",
#--docker-sysctls "{}"
docker_args = dict(
    docker_image            = dict(required=True,   type='str'),
    docker_pull_policy      = dict(required=False,  type='str', default=''),
    docker_privileged       = dict(required=False,  type='bool', default=False),
    docker_volumes          = dict(required=False,  type='str', default=''),
    url                     = dict(required=True,   type='str'),
    name                    = dict(required=True,   type='str'),
    registration_token      = dict(required=True,   type='str'),
    limit                   = dict(required=False,  type='str', default='0'),
    locked                  = dict(required=False,  type='bool', default=True),
    run_untagged            = dict(required=False,  type='bool', default=True),
    leave_runner            = dict(required=False,  type='bool', default=False),
    tag_list                = dict(required=False,  type='str', default=''),
    env                     = dict(required=False,  type='str', default=''),
    token                   = dict(required=False,  type='str', default=''),
    config                  = dict(required=False,  type='str', default='/etc/gitlab-runner/config.toml')
)
global_args = dict(
    address                 = dict(required=False, type= 'str', default=''),
    port                    = dict(required=False, type= 'str', default='9100')
)

ssh_args = dict(
    ssh_user                = dict(required=False,type=''),
    ssh_password            = dict(required=False,type=''),
    ssh_host                = dict(required=True,type=''),
    ssh_port                = dict(required=False,type=''),
    ssh_identity_file       = dict(required=False,type='')
)

#@todo: adding support of shell and ssh executor

result = dict(
    changed         = False,
    original_message='',
    message         =''
)

module_args.update(docker_args)
module_args.update(global_args)

module = AnsibleModule(
    argument_spec=module_args,
    supports_check_mode=True
)

#extract the config of the existing runner into temp file
def extract_runner_conf(args):
    try:
        inFile = open(args['config'])

        outFileName = '/tmp/extracted-' + str(uuid.uuid4()) + '.tmp'
        outFile = open(outFileName,'w')
        
        matched = False
        processed = False
        u = []
        for line in inFile:
            u.append(line)
            formatedMatch = re.escape(args['name'])
#            if re.match('listen_address = \"([0-9,\.]*:[0-9]{4})\"'):
#                outFile.write("".join(line))
            if re.match('.*'+formatedMatch, line):
                matched = True
                outFile.write("[[runners]]\n")
                outFile.write("".join(line))
                processed = True
            elif line.startswith("\n"):
                matched = False
            elif matched:
                outFile.write("".join(line))

        inFile.close
        outFile.close
        
        if not processed:
            module.fail_json(msg="cannot found "+args['name']+' section in '+args['config'], **result)
        return outFileName
    except Exception, err:
        module.fail_json(msg="Exception "+ type(err).__name__ + str(err) + " at line " + format(sys.exc_info()[-1].tb_lineno), **result)


#compare listen address between old config and the new
def compare_listen_adress_config(args):
    try:
        #if listen_address is not specified listen address will stay an empty string
        listen_address_old_config = ''
        inFile = open(args['config'])
        m = re.search('listen_address = \"([0-9,\.]*:[0-9]{4})\"', inFile ,re.MULTILINE)
        expectedListenAddress = writeOutListenAddress(args['address'], args['port'])
        if m :
            listen_address_old_config = m.group(0)
        compareResult = (listen_address_old_config == expectedListenAddress)

        if compareResult == False:
            result['message']='Difference detected in global config: diff -yBw '+listen_address_old_config+' '+ args['listen_address']
            return True
        else:
            result['message'] = 'Config is identical, no need to register or update'
            return False

    except Exception, err:
        module.fail_json(msg="Exception "+ type(err).__name__ + str(err) + " at line " + format(sys.exc_info()[-1].tb_lineno), **result)


# comparing the extracted conf with the new conf created by gitlab-runner
def compare_config(args):
    try:
        #check if runner already exist
        rc, out, err = module.run_command('gitlab-runner verify -n '+ args['name'])
        
        # if it exist then create a temp file to compare
        if rc == 0:
            #get the existing token
            cmdResult = Popen('gitlab-runner list',shell=True, stdout=PIPE, stderr=PIPE)
            cmdOut, cmdErr = cmdResult.communicate()

            for line in cmdErr.splitlines():
                formattedMatch = re.escape(args['name'])
                matched = re.match(formattedMatch, line)
                if matched:
                    args['token'] = re.search('Token.*=(.*) URL',line, re.MULTILINE).group(1)
                    break

            if not 'token' in args or not args['token']:
                module.fail_json(msg="compare_config: Can't Found Token of existing runner "+args['name'], **result)
            
            # extract config from config file
            nFile = extract_runner_conf(args)
            
            # Launch Register to create new temp config file
            args['config'] = '/tmp/runner-' + str(uuid.uuid4()) + '.tmp'
            args['url']='http://localhost'
            args['leave_runner'] = True
            open(args['config'], 'a').close() # touch
            
            tmpToken = runner_register(args, args['config'])
            #we delete global line not concerning the runner
            import fileinput
            for line in fileinput.input(args['config'],inplace =1):
                line = line.strip()
                #todo refactor
                if not 'concurrent =' in line and not 'check_interval =' in line and not 'listen_address =' in line :
                    print line

            #we unregister immediatly the new runner created for the test
            tmpArgs = args
            tmpArgs['token'] = tmpToken
            runner_unregister(tmpArgs)
            
            # and compare the two files with each other
            compareResult = filecmp.cmp(nFile,args['config'])
            
            # Error at comparing file
            if compareResult == False:
                result['message']='Difference detected in: diff -yBw '+nFile+' '+ args['config']
                return True
            else:
                result['message'] = 'Config is identical, no need to register or update'
                return False

                             
            # if not identical unregister the old one
            if compareResult == False:
                result['message']='Compare found difference'
                runner_unregister(args)
                args['changed'] = True
            else: # do nothing
                args['changed'] = False
            
            # clean tmp files
            os.remove(nFile)
            os.remove(args['config'])
            return True
        else:
            # the runner is not already in config, then yes, there is difference
            return True
    except Exception, err:
        module.fail_json(msg="Exception "+ type(err).__name__ + str(err) + " at line " + format(sys.exc_info()[-1].tb_lineno), **result)

# calling the right registerer depending of executor value
def runner_register(args,configFile='/etc/gitlab-runner/config.toml'):
    if args['executor'] == 'docker':
        return runner_register_docker(args,configFile)
    elif args['executor'] == 'docker+machine' or args['executor'] == 'docker-ssh' or args['executor'] == 'docker-ssh+machine':
        module.fail_json(msg='ERROR: ' + args['executor'] + ' is Deprecated',**result)
    elif args['executor'] == 'shell':
        return runner_register_shell(args,configFile)
    elif args['executor'] == 'ssh':
        return runner_register_ssh(args,configFile)
    else:
        module.fail_json(msg='ERROR: ' + args['executor'] + ' is not recognized',**result)

def getRunnerToken(name, configFile):
    cmdResult = Popen('gitlab-runner list -c "'+configFile+'"',shell=True, stdout=PIPE, stderr=PIPE)
    cmdOut, cmdErr = cmdResult.communicate()

    for line in cmdErr.splitlines():
        formatedMatch = re.escape(name)
        matched = re.match(formatedMatch, line)
        if matched:
            token = re.search('Token.*=(.*) URL',line, re.MULTILINE).group(1)
            return token
    return False

def writeOutListenAddress(address, port):
    "".join("listen_address = ",address,":", port)

def set_runner_listen_adress(args, configFile='/etc/gitlab-runner/config.toml'):
    try:
        inFile = open(configFile)
        outFileName = '/tmp/extracted-config' + str(uuid.uuid4()) + '.tmp'
        outFile = open(outFileName,'w')

        for line in inFile:

            m = re.search('listen_address = \"([0-9,\.]*:[0-9]{4})\"', inFile ,re.MULTILINE)
            if m :
                outFile.write(writeOutListenAddress(args['address'], args['port']))
            else :
                outFile.write("".join(line))
        #
        # m = re.search('listen_address = \"([0-9,\.]*:[0-9]{4})\"', inFile ,re.MULTILINE)
        # if m :
        #     listen_address_old_config = m.group(0)
        #
        # compareResult = (listen_address_old_config == args['listen_address'])

        outFile.close()
        inFile.close()

    except Exception, err:
        result['original_message'] = "Exception "+ type(err).__name__ + str(err) + " at line " + format(sys.exc_info()[-1].tb_lineno)
        module.fail_json(msg="Exception "+ type(err).__name__ + str(err) + " at line " + format(sys.exc_info()[-1].tb_lineno),**result)

def runner_register_docker(args, configFile='/etc/gitlab-runner/config.toml'):
    try:
        options = '--non-interactive'

        cmdArgs = ['gitlab-runner','register',options,'--url '+args['url'],
                   '--registration-token '+args['registration_token'],'--executor docker',
                   '--docker-image '+args['docker_image'],'--name '+args['name']]
        
        for key, value in docker_args.iteritems():
            # dont take url and registration token because the command line doesn't like when it don't come first
            if args[key]!='' and key != 'url' and key != 'registration_token' and key != 'docker_image' and key != 'name':
                # except for oneword arg which need '=' sign before value
                if value['type']=='bool':
                    if args[key] == True:
                        cmdArg = '--' + key.replace('_','-')+'=true'
                    else:
                        cmdArg = '--' + key.replace('_','-')+'=false'
                else:
                    cmdArg = '--' + key.replace('_','-') + ' '+ str(args[key])
    
                cmdArgs.append(cmdArg)

        if not module.check_mode:
            rc, out, err = module.run_command(' '.join(cmdArgs))
        else:
            result['msg']='msg:Execute Command:'+' '.join(cmdArgs)
            result['message']='m:Execute Command:'+' '.join(cmdArgs)
            result['original_message']='om: Execute Command:'+' '.join(cmdArgs)

        if not module.check_mode:
            result['changed']= True
            result['message']='runner '+ args['name'] + ' registered'
            tokenId = getRunnerToken( args['name'], configFile)
            if tokenId:
                return tokenId
            else:
                return False
        else:
            result['changed']= False
            return False
        
    except Exception, err:
        result['original_message'] = "Exception "+ type(err).__name__ + str(err) + " at line " + format(sys.exc_info()[-1].tb_lineno)
        module.fail_json(msg="Exception "+ type(err).__name__ + str(err) + " at line " + format(sys.exc_info()[-1].tb_lineno),**result)
    
def runner_register_shell(args):
    module.fail_json(msg="Not yet Implemented", **result)
    return False

def runner_register_ssh(args):
    module.fail_json(msg="Not yet implemented", **result)
    return False

def runner_unregister(args):
    cmdArgs = []
    if args['token']:
        cmdArgs.append("-t " + str(args['token']))
    elif args['name']:
        cmdArgs.append("-n " + args['name'])
    if args['url']:
        cmdArgs.append("-u " + args['url'])

    result['message']='execute: gitlab-runner unregister ' + ' '.join(cmdArgs)
    try:
        cmdResult = Popen("gitlab-runner unregister " + ' '.join(cmdArgs), shell=True)
    except Exception, err:
        module.fail_json(msg=err,**result)

def run_module():
    if not spawn.find_executable("gitlab-runner"):
        module.fail_json(msg="gitlab-runner not found: is it installed or in path?")
                             
    #delete all runner which no more exist on gitlab manager
    call(["gitlab-runner","verify","--delete"])
    
    if module.params['command'] == "register":
        #check if there is difference
        if compare_config(module.params) == True:
            if runner_register(module.params) == True:
                result['changed']= True
            else:
                result['changed'] = False
        #else doing nothing
        else:
            result['changed'] = False
            result['message']= "No differences detected, nothing to do"
    elif module.params['command'] == 'unregister':
        if compare_listen_adress_config(module.params):
            runner_unregister(module.params)
    elif module.params['command'] == 'listen_address' :
        if compare_listen_adress_config(module.params):
            set_runner_listen_adress(module.params)

    module.exit_json(**result)

def main():
    run_module()

if __name__ == '__main__':
    main()