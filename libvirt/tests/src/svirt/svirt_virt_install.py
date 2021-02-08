import os

from avocado.utils import process

from virttest import data_dir
from virttest import utils_selinux
from virttest import virsh
from virttest import utils_test
from virttest import utils_misc


def run(test, params, env):
    """
    Test svirt in virt-install.

    (1). Init variables.
    (2). Set selinux on host.
    (3). Set label of image.
    (4). run a virt-install command.
    (5). clean up.

    As this test only care whether the qemu-kvm process
    can access the image. It is not necessary to install
    a full os in a vm. Just verify the vm is alive after
    virt-install command is enough. Then we can save a lot
    of time and make this test independent from unattended_install.
    """
    # Get general variables.
    status_error = ('yes' == params.get("status_error", 'no'))
    host_sestatus = params.get("host_selinux", "enforcing")
    video_model = params.get('video_model', 'vga')
    nvram_o = params.get('nvram_o', None)
    # Get variables about seclabel for VM.
    sec_type = params.get("svirt_install_vm_sec_type", "dynamic")
    sec_model = params.get("svirt_install_vm_sec_model", "selinux")
    sec_label = params.get("svirt_install_vm_sec_label", None)
    sec_relabel = params.get("svirt_install_vm_sec_relabel", "yes")

    # Set selinux status on host.
    backup_sestatus = utils_selinux.get_status()
    utils_selinux.set_status(host_sestatus)

    # Set the image label.
    disk_label = params.get("svirt_install_disk_label", None)
    vm_name = params.get("main_vm", None)
    # svirt will prevent accessing via a symble link.
    data_path = data_dir.get_data_dir()
    real_data_path = os.path.realpath(data_path)
    image_path = os.path.join(real_data_path, "svirt_image")
    if virsh.domain_exists(vm_name):
        virsh.remove_domain(vm_name, nvram_o)
    if not os.path.exists(image_path):
        utils_test.libvirt.create_local_disk("file", path=image_path)

    try:
        utils_selinux.set_context_of_file(image_path, disk_label)
        cmd = "virt-install --name %s --import --disk" % vm_name
        cmd += " path=%s --ram '1024' " % image_path
        cmd += " --security"
        if sec_type == 'static':
            if sec_label is None:
                raise ValueError("Seclabel is not setted for static.")
            cmd += " type=static,label=%s" % (sec_label)
        elif sec_type == 'dynamic':
            cmd += " type=dynamic"
        else:
            raise ValueError("Security type %s is not supported."
                             % sec_type)
        if sec_relabel is not None:
            cmd += ",relabel=%s" % sec_relabel

        cmd += " --noautoconsole --graphics vnc --video %s" % video_model
        process.run(cmd, timeout=600, ignore_status=True)

        def _vm_alive():
            return virsh.is_alive(vm_name)
        if (utils_misc.wait_for(_vm_alive, timeout=5)):
            if status_error:
                test.fail('Test succeeded in negative case.')
        else:
            if not status_error:
                test.fail("Test failed in positive case.")
    finally:
        # cleanup
        utils_selinux.set_status(backup_sestatus)
        if virsh.domain_exists(vm_name):
            virsh.remove_domain(vm_name, nvram_o)
        if not os.path.exists(image_path):
            utils_test.libvirt.delete_local_disk("file", path=image_path)
