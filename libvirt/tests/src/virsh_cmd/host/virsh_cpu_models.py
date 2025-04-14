import logging as log
from six import itervalues

from avocado.utils import process

from virttest import ssh_key
from virttest import virsh
from virttest import libvirt_vm
from virttest.libvirt_xml import capability_xml
from virttest.utils_test import libvirt as utlv


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def compare_cpu_model_with_qemu(test, params, virsh_cpu_model_result, qemu_cmd):
    """
    Compare the libvirt cpu model same with the model in qemu-kvm.

    :params: test:  test object
    :params: params, cfg parameter dict.
    :params: virsh_cpu_model_result:virsh cpu-model result
    :params: qemu_cmd: qemu cmd to get cpu model name.
    """
    arch_option = params.get("arch_option")
    if arch_option == "arch_x86":
        skip_list = eval(params.get("skip_list", []))
        qemu_models = process.run(qemu_cmd, ignore_status=False, shell=True).stdout_text.strip().split("\n")
        virsh_cpu_model_result = virsh_cpu_model_result.stdout_text.strip().split("\n")

        for qemu_model in qemu_models:
            if qemu_model not in {*virsh_cpu_model_result, *skip_list}:
                test.fail("Expected the model of qemu-kvm:%s in virsh cpu-model result" % qemu_model)
        test.log.debug("All the cpu models of qemu-kvm are contained in virsh cpu-models.")
    else:
        msg = params.get("msg", "")
        if msg not in virsh_cpu_model_result.stdout_text:
            test.fail("Expected '%s' in virsh cpu-models result" % msg)


def run(test, params, env):
    """
    Test command virsh cpu-models
    """
    cpu_arch = params.get("cpu_arch", "")
    option = params.get("option", "")
    status_error = "yes" == params.get("status_error", "no")
    remote_ref = params.get("remote_ref", "")
    connect_uri = libvirt_vm.normalize_connect_uri(params.get("connect_uri",
                                                              "default"))
    qemu_cmd = params.get("check_qemu_cpu_supported_cmd", "")

    if remote_ref == "remote":
        remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
        remote_pwd = params.get("remote_pwd", None)

        if 'EXAMPLE.COM' in remote_ip:
            test.cancel("Please replace '%s' with valid remote ip" % remote_ip)

        ssh_key.setup_ssh_key(remote_ip, "root", remote_pwd)
        connect_uri = libvirt_vm.complete_uri(remote_ip)

    arch_list = []
    if not cpu_arch:
        try:
            capa = capability_xml.CapabilityXML()
            guest_map = capa.get_guest_capabilities()
            guest_arch = []
            for v in list(itervalues(guest_map)):
                guest_arch += list(v.keys())
            for arch in set(guest_arch):
                arch_list.append(arch)
        except Exception as e:
            test.error("Fail to get guest arch list of the host"
                       " supported:\n%s" % e)
    else:
        arch_list.append(cpu_arch)
    for arch in arch_list:
        logging.debug("Get the CPU models for arch: %s" % arch)
        result = virsh.cpu_models(arch, options=option, uri=connect_uri,
                                  ignore_status=True, debug=True)
        utlv.check_exit_status(result, expect_error=status_error)
        compare_cpu_model_with_qemu(test, params, result, qemu_cmd)
