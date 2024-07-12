from virttest import utils_libvirtd
from virttest import data_dir
from virttest.libvirt_xml import vm_xml

import logging
import subprocess


def create_scripts(vmxml, num_scripts, timeout):
    """
    Create scripts for creating and destroying
    VMs. We create separate scripts so we can
    execute them concurrently, and overcome
    python's GIL

    :param vmxml: Base xml for the VMs
    that will be created

    :param num_scripts: number of scripts to
    create

    :param timeout: amount of time to spend creating
    and destroying VMs
    """
    tmp_dir = data_dir.get_tmp_dir(public=False)
    vm_name = vmxml.vm_name
    script_names = []

    for i in range(num_scripts):
        xml_path = tmp_dir + "/xml_" + str(i) + ".xml"
        sh_path = tmp_dir + "/script_" + str(i) + ".sh"

        # SELinux does not allow multiple VM operations the same qcow2 files
        vmxml.remove_all_disk()

        for item in ["loader", "nvram"]:
            if item in str(vmxml):
                vmxml.xmltreefile.remove_by_xpath("/os/%s" % item, remove_all=True)

        with open(xml_path, "w") as outfile:
            outfile.write(str(vmxml))

        logging.debug("Written vm xml file to {}".format(xml_path))
        logging.info("XML Contents: \n{}".format(str(vmxml)))

        with open(sh_path, "w") as outfile:
            script = """
                end=$((SECONDS+{}));
                while [ $SECONDS -lt $end ] ; do
                    virsh create {} || continue ;
                    virsh destroy {} ;
                done ;
                """.format(timeout, xml_path, vm_name)
            outfile.write(script)

        logging.debug("Written script file to {}".format(sh_path))

        script_names.append(sh_path)

    return script_names


def get_pids_for(names):
    """
    Given a list of names, retrieve the
    PIDs for matching processes

    Sort of equivalent to: 'ps aux | grep name'

    :param names: List of process names to look for
    """

    ps_cmd = subprocess.Popen(["ps", "aux"], stdout=subprocess.PIPE)
    ps_cmd.wait()

    ps_output = str(ps_cmd.stdout.read()).split("\\n")
    relevant_procs = [x for x in ps_output for n in names if n in x]
    relevant_procs = [x.split() for x in relevant_procs]
    relevant_pids = [int(x[1]) for x in relevant_procs]
    relevant_pids.sort()
    logging.info("Processes and Pids matching {}: {}".format(names, relevant_procs))

    return relevant_pids


def get_libvirt_pids(test, daemon):
    """
    Given a Libvirtd daemon object, retrieve the
    pids relevant to the libvirt services

    :param test: Avocado test object
    :param daemon: Avocado-vt libvirtd daemon
    object. Provides list of libvirt services
    """
    if not daemon.is_running():
        test.fail("No libvirt daemon running")

    return get_pids_for(daemon.service_list)


def run(test, params, env):
    """
    This test ensures that VMs can be created concurrently.
    Test Process:
        1) Create bash scripts to create and destroy transient VMs
        2) Execute the scripts simultaneously for a given number of seconds
        3) Check that libvirt daemon(s) have not changed PID
        4) Check to ensure there are no orphan VMs
    """

    daemon = utils_libvirtd.Libvirtd()
    pids_before_test = get_libvirt_pids(test, daemon)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    num_threads = int(params.get("num_threads", 3))
    wait_time = int(params.get("run_time", 60))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    scripts = create_scripts(vmxml, num_threads, wait_time)

    processes = []
    for i in range(num_threads):
        process = subprocess.Popen(["/bin/bash", scripts[i]], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        processes.append(process)

    exit_codes = [p.wait() for p in processes]

    for i in range(len(exit_codes)):
        if exit_codes[i] != 0:
            fail = processes[i]
            logging.debug("Script did not exit cleanly")
            logging.debug("\tstderr: {}".format(fail.stderr.read()))
            logging.debug("\tstdout: {}".format(fail.stdout.read()))

    if [x for x in exit_codes if x != 0] != []:
        test.fail("Test Failed with script errors")

    pids_after_test = get_libvirt_pids(test, daemon)
    if pids_before_test != pids_after_test:
        logging.debug("Pids Before Test: {}".format(pids_before_test))
        logging.debug("Pids After Test: {}".format(pids_after_test))
        test.fail("Libvirt pids changed")

    vm_pids = get_pids_for([vm_name])
    if vm_pids != []:
        test.fail("Orphan VM(s): PID(s) {} belonging to {}".format(vm_pids, vm_name))
