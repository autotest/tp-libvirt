import pwd
import os

from virttest import libvirt_version
from virttest import remote
from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_unprivileged


def run(test, params, env):
    """
    This case is to verify that if the source path of guest agent is not
    specified in session mode, it will be set automatically by libvirt,
    and the path matches certain pattern.
    """
    def check_src_path(domid, vm_name, dir_name, chn_dir, tgt_name,
                       max_dir_length, cmp_path=None):
        """Check the source path

        :param domid: domain id
        :param vm_name: vm name
        :param dir_name: directory name
        :param chn_dir: libvirt dir for channel
        :param tgt_name: target name
        :param max_dir_length: max directory length
        :param cmp_path: the path to compare, defaults to None
        """
        vm_info = f"{domid}-{vm_name}"
        tmp_path = os.path.join(dir_name, chn_dir, vm_info, tgt_name)
        if cmp_path and tmp_path != cmp_path:
            test.fail(f"The channel device's source path should be {tmp_path} "
                      f"but got {cmp_path}")
        if len(tmp_path) > max_dir_length:
            test.fail(f"The length of source path '{tmp_path}' should not "
                      f"exceed {max_dir_length}.")

    libvirt_version.is_libvirt_feature_supported(params)

    chn_dir = params.get("chn_dir", "libvirt/qemu/run/channel")
    kube_dir = params.get("kube_dir", "/var/run/kubevirt-private")
    max_dir_length = int(params.get("max_dir_length", "107"))
    new_vm_name = 'vm' + utils_misc.generate_random_string(22)
    test_passwd = params.get('test_passwd', '')
    test_user = params.get('test_user', '')
    tgt_name = params.get("tgt_name", "org.qemu.guest_agent.0")
    up_domid = params.get("up_domid", "2147483647")
    vm_name = params.get('unpr_vm_name')

    runtime_dir = remote.RemoteRunner(
        username=test_user, password=test_passwd, host='localhost').run(
            'echo $XDG_RUNTIME_DIR').stdout_text.strip()
    if not runtime_dir:
        runtime_dir = os.path.join(pwd.getpwnam(test_user).pw_dir, '.cache')

    unpr_vm_args = {'username': params.get('username'),
                    'password': params.get('password')}
    vm = libvirt_unprivileged.get_unprivileged_vm(vm_name, test_user,
                                                  test_passwd,
                                                  **unpr_vm_args)
    virsh_ins = virsh.Virsh(uri=vm.connect_uri)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
        vm_name, virsh_instance=virsh_ins)
    backup_xml = vmxml.copy()
    try:
        test.log.info("TEST_STEP: Create a vm in session mode.")
        if vm.is_alive():
            vm.destroy()
        virsh_ins.domrename(
            vm.name, new_vm_name, debug=True, ignore_status=False)
        vm_new = libvirt_unprivileged.get_unprivileged_vm(
            new_vm_name, test_user, test_passwd, **unpr_vm_args)

        test.log.info("TEST_STEP: Prepare guest agent.")
        vm_new.prepare_guest_agent(serial=True)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
            new_vm_name, virsh_instance=virsh_ins)
        test.log.debug(f"vmxml after updating: {vmxml}")
        inactive_src = vmxml.devices.by_device_tag(
            "channel")[0].fetch_attrs().get('sources')
        if inactive_src and inactive_src[0]['attrs'].get('path'):
            test.fail(
                "There should be no specified path in inactive domain xml.")

        test.log.info("TEST_STEP: Execute a guest agent command.")
        virsh_ins.domtime(new_vm_name, debug=True, ignore_status=False)

        test.log.info("TEST_STEP: Execute a guest agent command.")
        vmxml = vm_xml.VMXML.new_from_dumpxml(
            new_vm_name, virsh_instance=virsh_ins)

        test.log.info("TEST_STEP: Check the guest agent socket path.")
        chn_obj = vmxml.devices.by_device_tag("channel")[0]
        test.log.debug(f"channel xml: {chn_obj}")
        src_path = chn_obj.fetch_attrs()['sources'][0]['attrs']['path']
        check_src_path(virsh_ins.domid(new_vm_name).stdout_text.strip(),
                       vm_new.name[:20], runtime_dir, chn_dir, tgt_name,
                       max_dir_length, cmp_path=src_path)

        test.log.info("TEST_STEP: Check the length of the socket path "
                      "which will be used in cnv.")
        check_src_path(up_domid, vm_new.name[:20], kube_dir, chn_dir, tgt_name,
                       max_dir_length)

    finally:
        vm_new.destroy()
        virsh_ins.domrename(vm_new.name, vm.name, debug=True)
        backup_xml.sync(virsh_instance=virsh_ins)
