#!/usr/bin/python

from ansible.module_utils.basic import AnsibleModule
#from ansible.modules.commands import shell
from subprocess import Popen, call, PIPE, STDOUT
from distutils import spawn
import sys
import uuid
import re
import filecmp
import os
from sys import stderr

#define the available arguments/parameters that user can pass to the module
# command can be register|unregister
# executor can be docker|ssh|shell
module_args = dict(
    command                 = dict(required=True,   type='str'), 
    executor                = dict(required=True,   type='str')
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
    url                     = dict(required=True,   type='str'),
    docker_image            = dict(required=True,   type='str'),
    name                    = dict(required=True,   type='str'),
    registration_token      = dict(required=True,   type='str'),
    limit                   = dict(required=False,  type='str', default='0'),
    docker_pull_policy      = dict(required=False,  type='str', default=''),
    locked                  = dict(required=False,  type='str', default='false'),
    docker_privileged       = dict(required=False,  type='str', default='false'),
    run_untagged            = dict(required=False,  type='str', default='True'),
    tag_list                = dict(required=False,  type='str', default=''),
    env                     = dict(required=False,  type='str', default=''),
    config_file             = dict(required=False,  type='str', default='/etc/gitlab-runner/config.toml')
)

#@todo: adding support of shell and ssh executor

result = dict(
    changed         = False,
    original_message='',
    message         =''
)

module_args.update(docker_args)

module = AnsibleModule(
    argument_spec=module_args,
    supports_check_mode=True
)


#extract the config of the existing runner into temp file
def extract_runner_conf(args):
    try:
        result['message']='extracting config of runner '+args['name']
        inFile = open(args['config_file'])
        outFileName = '/tmp/runner' + uuid.uuid4() + '.tmp'
        outFile = open(outFileName)
        matched = False
        for line in inFile:
            if line.startswith("name = \"" + args['name'] + "\""):
                matched = True
                outFile.write("[[runners]]\n".join(line))
            elif line.startswith("\n"):
                matched = False
                return 0
            elif matched:
                outFile.write("".join(line))
    
        inFile.close
        outFile.close
        return outFileName
    except:
        result['message']="exception in extract_runner_conf"
        module.fail_json(msg="Unexpected Error in compare_config", **result)
        
def compare_config(args):
    try:
        result['message']='Comparing config...'
        configFileBackup = args['config_file']
        
        #check if runner already exist
        if call( ["gitlab-runner", "verify", "-n " + args['name']] ) == 0:
            result['message']='runner ' + args['name'] + ' exist'
            #get the existing token
            cmd = Popen("gitlab-runner list", shell=True,stdout=PIPE)
            for line in cmd.stdout:
                if args['name'] in line:
                    try:
                        args['runner_token'] = re.search('.*Token=(.+?) URL=.*$',line).group(1)
                    except AttributeError:
                        module.fail_json(msg="Can't Found Token of existing runner", **result)
            
            if not args['runner_token']:
                module.fail_json(msg="Can't Found Token of existing runner", **result)
                
            # Launch Register to create new temp config file
            args['config_file'] = '/tmp/' + uuid.uuid4() + '.tmp' 
            runner_register(args)
            
            # extract config from config file
            nFile = extract_runner_conf(args)
            
            # and compare with temp config file
            compareResult = filecmp.cmp(nFile,args['config_file'])
            
            # if not identical unregister the old one
            if compareResult == False:
                runner_unregister(args)
                args['changed'] = True
            else: # do nothing
                args['changed'] = False
            
            # clean tmp file
            os.remove(args['tmp_file'])
            # 
            args['config_file'] = configFileBackup
            return args['changed']
        else:
            result['message']='runner '+ args['name'] + " doesn't exist"
            runner_register(args)
            
    except Exception, err:
        result['message']= "Exception "+ type(err).__name__ + str(err) + " at line " + format(sys.exc_info()[-1].tb_lineno)
        module.fail_json(msg="Unexpected Error in compare_config", **result)

# calling the right registerer depending of executor value
def runner_register(args):
    if args['executor'] == 'docker':
        runner_register_docker(args)
    elif args['executor'] == 'docker+machine' or args['executor'] == 'docker-ssh' or args['executor'] == 'docker-ssh+machine':
        return 'ERROR' + args['executor'] + ' is Deprecated'
    elif args['executor'] == 'shell':
        runner_register_shell(args)
    elif args['executor'] == 'ssh':
        runner_register_ssh(args)

def runner_register_docker(args):
    cmdArgs = ['gitlab-runner','register','--non-interactive']
    cmdArgs.append('--url '+args['url'])
    cmdArgs.append('--executor '+args['executor'])
    cmdArgs.append('--registration-token '+args['registration_token'])
    cmdArgs.append('--name '+args['name'])
    cmdArgs.append('--docker-image '+args['docker_image'])
    cmdArgs.append('--docker-pull-policy '+args['docker_pull_policy'])
    cmdArgs.append('--run-untagged '+args['run_untagged'])
    cmdArgs.append('--tag-list '+args['tag_list'])
    cmdArgs.append('--locked '+args['locked'])
    cmdArgs.append('--docker-privileged '+args['docker_privileged'])
    if args['env']: cmdArgs.append('--env '+args['env'])
    
    result['message']='COMMAND:' + ' '.join(cmdArgs)
    #cmdResult = subprocess.check_output(cmdArgs, stderr=subprocess.STDOUT, shell=True,universal_newlines=True)
    cmdResult = Popen(' '.join(cmdArgs), shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE)

    if cmdResult==0:
        result['changed']=True
    else:
        stdout,stderr = cmdResult.communicate()
        module.fail_json(msg=stderr, **result)   
    
    
    
def runner_register_shell(args):
    module.fail_json(msg="Not yet Implemented", **result)
    return False

def runner_register_ssh(args):
    module.fail_json(msg="Not yet implemented", **result)
    return False

def runner_unregister(args):
    cmdArgs = []
    if args['token']:
        cmdArgs.append("-t " + args['token'])
    if args['name']:
        cmdArgs.append("-n " + args['name'])
    if args['manager_url']:
        cmdArgs.append("-u " + args['manager_url'])
    if args['all_runners']:
        cmdArgs.append("--all-runners")

    call("gitlab-runner unregister " + ' '.join(cmdArgs))

def run_module():
    try:
        if not spawn.find_executable("gitlab-runner"):
            module.fail_json(msg="gitlab-runner not found: is it installed or in path?")
                                 
        #delete all runner which no more exist on gitlab manager
        call(["gitlab-runner","verify","--delete"])
        
        if module.params['command'] == "register":
            if compare_config(module.params) == True:
                if runner_register(module.params) == True:
                    result['changed']='has_changed'
            else:
                result['changed'] = False
        elif module.params['command'] == 'unregister':
            runner_unregister(module.params)
        
        module.exit_json(**result)
    except Exception, err:
        result['message']= "Exception "+ type(err).__name__ + str(err) + " at line " + format(sys.exc_info()[-1].tb_lineno)
        module.fail_json(msg="Unexpected Error", **result)
        
        

def main():
    run_module()

if __name__ == '__main__':
    main()