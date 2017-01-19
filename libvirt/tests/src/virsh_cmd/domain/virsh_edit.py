import logging
import subprocess
import re

from avocado.core import exceptions
from avocado.utils import process

from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test command: virsh edit.

    The command can edit XML configuration for a domain
    1.Prepare test environment,destroy or suspend a VM.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Perform virsh edit operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    libvirtd_stat = params.get("libvirtd", "on")
    vm_ref = params.get("edit_vm_ref")
    extra_option = params.get("edit_extra_param", "")
    status_error = params.get("status_error")
    edit_element = params.get("edit_element", "vcpu")
    bashrc_file = params.get("bashrc_file", "/root/.bashrc")
    editor_cfg = params.get("editor_cfg_str", "export EDITOR=vi")

    # check for vi as editor in bashrc to support regex used in
    # this tests for editing vmxml
    logging.debug("checking the default editor")
    try:
        check_flag = False
        cmd_output = process.system_output("cat %s" % bashrc_file,
                                           shell=True).strip().split('\n')
        for each_cfg in cmd_output:
            if editor_cfg in each_cfg.strip():
                if not re.search("^#", each_cfg.strip()):
                    check_flag = True
                    logging.debug("vi is already configured as editor")
                    break
        if not check_flag:
            logging.debug("configuring vi as default editor")
            with open(bashrc_file, "a") as myfile:
                myfile.write("\n%s\n" % editor_cfg)
            myfile.close()
            subprocess.check_output(['bash', '-c', "source %s" % bashrc_file])
    except process.CmdError:
        logging.debug("%s file doesn't exist" % bashrc_file)
        try:
            process.system("echo %s > %s" % (editor_cfg,
                                             bashrc_file), shell=True)
            subprocess.check_output(['bash', '-c', "source %s" % bashrc_file])
        except Exception, info:
            raise exceptions.TestSkipError("Test requires vi editor as "
                                           "default - %s" % info)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    libvirtd = utils_libvirtd.Libvirtd()

    def edit_vcpu(source):
        """
        Modify vm's cpu information by virsh edit command.

        :param source : virsh edit's option.
        :return: True if edit successed,False if edit failed.
        """
        vcpucount_result = virsh.vcpucount(vm_name,
                                           options="--config --maximum")
        if vcpucount_result.exit_status:
            # Fail back to libvirt_xml way to test vcpucount.
            original_vcpu = str(vmxml.vcpu)
        else:
            original_vcpu = vcpucount_result.stdout.strip()

        expected_vcpu = str(int(original_vcpu) + 1)
        top_mode = {}
        if not status_error == "yes":
            # check if topology is defined and change vcpu accordingly
            try:
                vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(source)
                topology = vmxml_backup.get_cpu_topology()
                cores = topology['cores']
                threads = topology['threads']
                sockets = str(topology['sockets'])
                old_topology = "<topology sockets='%s' cores='%s' threads='%s'\/>" % (
                    sockets, cores, threads)
                sockets = str(int(topology['sockets']) + 1)
                new_topology = "<topology sockets='%s' cores='%s' threads='%s'\/>" % (
                    sockets, cores, threads)
                top_mode = {"edit": r":%s /<topology .*\/>/" + new_topology,
                            "recover": r":%s /<topology .*\/>/" + old_topology}
                expected_vcpu = str(int(sockets) * int(cores) * int(threads))
            except:
                expected_vcpu = str(int(original_vcpu) + 1)
        dic_mode = {
            "edit": r":%s /[0-9]*<\/vcpu>/" + expected_vcpu + r"<\/vcpu>",
            "recover": r":%s /[0-9]*<\/vcpu>/" + original_vcpu + r"<\/vcpu>"}
        if top_mode:
            status = libvirt.exec_virsh_edit(source, [top_mode["edit"],
                                                      dic_mode["edit"]])
        else:
            status = libvirt.exec_virsh_edit(source, [dic_mode["edit"]])
        logging.info(status)
        if not status:
            vmxml.sync()
            return status
        if libvirtd_stat == "off":
            return False
        if params.get("paused_after_start_vm") == "yes":
            virsh.resume(vm_name, ignore_status=True)
            virsh.destroy(vm_name)
        elif params.get("start_vm") == "yes":
            virsh.destroy(vm_name)
        new_vcpus = str(vm_xml.VMXML.new_from_inactive_dumpxml(vm_name).vcpu)
        # Recover cpuinfo
        # Use name rather than source, since source could be domid
        if top_mode:
            status = libvirt.exec_virsh_edit(vm_name, [top_mode["recover"],
                                                       dic_mode["recover"]])
        else:
            status = libvirt.exec_virsh_edit(vm_name, [dic_mode["recover"]])
        vmxml.sync()
        if status and new_vcpus != expected_vcpu:
            return False
        return status

    def edit_iface(vm_name):
        """
        Modify vm's interface information by virsh edit command.
        """
        iface_type = params.get("iface_type")
        iface_model = params.get("iface_model")
        edit_error = "yes" == params.get("edit_error", "no")
        if iface_type:
            edit_cmd = (r":%s /<interface type=.*>/<interface type='{0}'>/"
                        "".format(iface_type))
            status = libvirt.exec_virsh_edit(vm_name, [edit_cmd])
        elif iface_model:
            edit_cmd = (r":/<interface/,/<\/interface>/s/<model type=.*\/>/"
                        "<model type='%s'\/>/" % iface_model)
            status = libvirt.exec_virsh_edit(vm_name, [edit_cmd])

        if not status and not edit_error:
            logging.error("Expect success, but failure")
            return False
        if edit_error and status:
            logging.error("Expect error, but success")
            return False

        # Destroy domain and start it to check if vm can be started
        start_error = "yes" == params.get("start_error", "no")
        vm.destroy()
        ret = virsh.start(vm_name, ignore_status=True)
        if start_error and not ret.exit_status:
            logging.error("Vm started unexpectedly")
            return False
        if not start_error and ret.exit_status:
            logging.error("Vm failed to start")
            return False
        return True

    def edit_memory(source):
        """
        Modify vm's maximum and current memory(unit and value).

        :param source: virsh edit's option.
        :return: True if edit successed,False if edit failed.
        """
        mem_unit = params.get("mem_unit", "K")
        mem_value = params.get("mem_value", "1048576")
        mem_delta = params.get("mem_delta", 1000)
        edit_cmd = []
        del_cmd = r":g/currentMemory/d"
        edit_cmd.append(del_cmd)
        update_cmd = r":%s/<memory unit='KiB'>[0-9]*<\/memory>/<memory unit='"
        update_cmd += mem_unit + "'>" + mem_value + r"<\/memory>"
        edit_cmd.append(update_cmd)
        try:
            expected_mem = int(utils_misc.normalize_data_size(
                mem_value + mem_unit, 'K').split('.')[0])
        except ValueError:
            logging.error("Fail to translate %s to KiB", mem_value + mem_unit)
            return False
        logging.debug("Expected max memory is %s", expected_mem)
        status = libvirt.exec_virsh_edit(source, edit_cmd)
        try:
            if status:
                # Restart vm to check memory value
                virsh.destroy(vm_name)
                virsh.start(vm_name)
                new_mem = vm.get_max_mem()
                if new_mem - expected_mem > int(mem_delta):
                    logging.error("New max memory %s is not excepted", new_mem)
                    return False
        except Exception, e:
            logging.error("Error occured when check domain memory: %s", e)
            return False
        return status

    def edit_rng(vm_name):
        """
        Modify rng device in xml.
        """
        rng_model = params.get("rng_model")
        rng_backend = params.get("rng_backend")
        backend_model = params.get("backend_model")
        backend_type = params.get("backend_type")
        edit_error = "yes" == params.get("edit_error", "no")
        edit_cmd = []
        del_cmd = r":g/<rng.*<\/rng>/d"
        edit_cmd.append(del_cmd)
        if backend_type:
            bc_type = "type='%s'" % backend_type
        else:
            bc_type = ""
        update_cmd = (r":/<devices>/s/$/<rng model='%s'>"
                      "<backend model='%s' %s>%s<\/backend><\/rng>"
                      % (rng_model, backend_model,
                         bc_type, rng_backend))
        edit_cmd.append(update_cmd)
        status = libvirt.exec_virsh_edit(vm_name, edit_cmd)
        vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        if not libvirtd.is_running():
            logging.error("libvirtd isn't running")
            return False
        if not status and not edit_error:
            logging.error("Expect success, but failure")
            return False
        if edit_error and status:
            logging.error("Expect error, but success")
            return False
        # Destroy domain and start it to check if vm can be started
        start_error = "yes" == params.get("start_error", "no")
        vm.destroy()
        ret = virsh.start(vm_name, ignore_status=True)
        if start_error and not ret.exit_status:
            logging.error("Vm started unexpectedly")
            return False
        if not start_error and ret.exit_status:
            logging.error("Vm failed to start")
            return False
        return True

    # run test case
    if libvirtd_stat == "off":
        libvirtd.stop()

    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "uuid":
        vm_ref = domuuid
    elif vm_ref == "name":
        vm_ref = vm_name
    if extra_option:
        vm_ref += extra_option

    try:
        if edit_element == "vcpu":
            status = edit_vcpu(vm_ref)
        elif edit_element == "memory":
            status = edit_memory(vm_ref)
        elif edit_element == "iface":
            status = edit_iface(vm_name)
        elif edit_element == "rng":
            status = edit_rng(vm_name)
        else:
            raise exceptions.TestSkipError("No edit method for %s" % edit_element)
        # check status_error
        if status_error == "yes":
            if status:
                raise exceptions.TestFail("Run successfully with wrong command!")
        elif status_error == "no":
            if not status:
                raise exceptions.TestFail("Run failed with right command")
    finally:
        # recover libvirtd service start
        if libvirtd_stat == "off":
            libvirtd.start()
        # Recover VM
        vm.destroy()
        vmxml.sync()
