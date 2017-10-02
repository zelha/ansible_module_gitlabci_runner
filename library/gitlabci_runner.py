#!/usr/bin/python
#-*- coding: utf-8 -*-

from ansible.module_utils.basic import AnsibleModule
#from ansible.modules.commands import shell
from subprocess import Popen, call, PIPE, STDOUT, check_output, CalledProcessError
from distutils import spawn
import sys
import shlex
import uuid
import re
import filecmp
import os
from sys import stderr
from ansible.module_utils import shell

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
    run_untagged            = dict(required=False,  type='str', default='true'),
    tag_list                = dict(required=False,  type='str', default=''),
    env                     = dict(required=False,  type='str', default=''),
    runner_token            = dict(required=False,  type='str', default=''),
    config                  = dict(required=False,  type='str', default='/etc/gitlab-runner/config.toml')
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
        try:
            inFile = open(args['config'])
        except OSError, err:
            module.fail_json(msg="Exception "+ type(err).__name__ + str(err) + " at line " + format(sys.exc_info()[-1].tb_lineno), **result)
        
        outFileName = '/tmp/extracted-' + str(uuid.uuid4()) + '.tmp'
        result['message']='create file ' + outFileName
        outFile = open(outFileName,'w')
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
    except Exception, err:
        module.fail_json(msg="Exception "+ type(err).__name__ + str(err) + " at line " + format(sys.exc_info()[-1].tb_lineno), **result)
        
def compare_config(args):
    try:
        result['message']='Comparing config...'
        
        #check if runner already exist
        result['message'] = "call gitlab-runner verify"
        cmdResult = call( ["gitlab-runner", "verify", "-n "+ args['name']], stderr=STDOUT, shell=True)
        if  cmdResult == 0:
            result['message']='runner ' + args['name'] + ' exist'
            
            #get the existing token
            cmdResult = Popen('gitlab-runner list',stdout=PIPE, stderr=PIPE,shell=True)
            
            for line in cmdResult.stderr:
                if args['name'] in line:
                    try:
                        result['message']= 'searching for token in: '+line
                        runner_token = re.search('Token.*=(.*) URL',line, re.MULTILINE).group(1)
                    except AttributeError:
                        module.fail_json(msg="1:Can't Found Token of existing runner", **result)
            
            if not runner_token:
                module.fail_json(msg="2:Can't Found Token of existing runner", **result)
                
            # Launch Register to create new temp config file
            args['config'] = '/tmp/runner-' + str(uuid.uuid4()) + '.tmp'
            open(args['config'], 'a').close()
            
            args['runner_token'] = runner_token
            runner_register(args)
            
            # extract config from config file
            try:
                nFile = extract_runner_conf(args)
            except OSError, err:
                module.fail_json(msg="File not found in extract_runner" + args['config'])
            # and compare with temp config file
            try:
                compareResult = filecmp.cmp(nFile,args['config'])
            except OSError, err:
                module.fail_json(msg="File not found in filecmp" + args['config'])
            
            
            runner_unregister(args)
            module.fail_json(msg='compare should work'+compareResult)
                             
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
            # 
            return True
        else:
            result['message']='runner '+ args['name'] + " doesn't exist"
            module.fail_json(msg="Doesn't entered in compare", **result)
            try:
                runner_register(args)
            except OSError, err:
                module.fail_json(msg="File not found in runner_register" + args['config'])
    except Exception, err:
        module.fail_json(msg="Exception "+ type(err).__name__ + str(err) + " at line " + format(sys.exc_info()[-1].tb_lineno), **result)

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
    try:
        cmdArgs = ['gitlab-runner','register','--non-interactive','--url '+args['url'],
                   '--registration-token '+args['registration_token'],'--executor docker',
                   '--docker-image '+args['docker_image'],'--name '+args['name']]
        for key, value in docker_args.iteritems():
            # dont take url and registration token because the command line doesn't like when it don't come first
            if args[key]!='' and key != 'url' and key != 'registration_token' and key != 'docker_image' and key != 'name':
                # except for oneword arg which doesn't wait for value
                if key == 'docker_privileged' and key == 'locked' and key == 'leave_runner':
                     if args[key] == True:
                         cmdArg = '--' + key.replace('_','-')
                else:
                    cmdArg = '--' + key.replace('_','-') + ' '+ args[key]
    
                cmdArgs.append(cmdArg)
        
        
        result['message']=' '.join(cmdArgs)
        cmdResult = Popen(' '.join(cmdArgs), shell=True, stdin=PIPE,stdout=PIPE, stderr=PIPE)
        out, err = cmdResult.communicate()
        
        if cmdResult.returncode == 0:
            result['changed']=True
            result['message']='runner '+ args['name'] + ' registered'
            return True
        else:
            result['message']=' '.join(cmdArgs)+"\r\n"+out
            module.fail_json(msg=err, **result)
        
        return False 
    except Exception, err:
        module.fail_json(msg="Exception "+ type(err).__name__ + str(err) + " at line " + format(sys.exc_info()[-1].tb_lineno), **result)
    
def runner_register_shell(args):
    module.fail_json(msg="Not yet Implemented", **result)
    return False

def runner_register_ssh(args):
    module.fail_json(msg="Not yet implemented", **result)
    return False

def runner_unregister(args):
    cmdArgs = []
    if args['runner_token']:
        cmdArgs.append("-t " + args['runner_token'])
    if args['name']:
        cmdArgs.append("-n " + args['name'])
    if args['url']:
        cmdArgs.append("-u " + args['url'])
    #if args['all_runners']:
    #    cmdArgs.append("--all-runners")
    result['message']='execute: gitlab-runner unregister ' + ' '.join(cmdArgs)
    try:
        call("gitlab-runner unregister " + ' '.join(cmdArgs))
    except Exception, err:
        module.fail_json(msg=err,**result)

def run_module():
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

def main():
    run_module()

if __name__ == '__main__':
    main()