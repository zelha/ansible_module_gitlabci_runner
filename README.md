# ansible_module_gitlabci_runner
ansible module to register gitlabci-runner easier and idempotently  

For the moment only support docker executor.

You can see an example of how using it in ansible in the [play.yml file](play.yml)

# How install it
put the [library/gitlabci_runner.py](library/gitlabci_runner.py) in your library folder at root of your playbook or in your `ANSIBLE_LIBRARY` [see ansible doc](http://docs.ansible.com/ansible/latest/dev_guide/developing_modules.html#welcome)
