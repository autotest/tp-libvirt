import os

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import utils_misc


def run(test, params, env):
    """
    Test for virt-clone, it is used to clone a guest
    to another with given name.

    (1). Get source guest and destination guest name.
    (2). Clone source guest to dest guest with virt-clone.
    (3). Check the dest guest.
    """
    # Get the full path of virt-clone command.
    try:
        VIRT_CLONE = process.run("which virt-clone", shell=True).stdout_text.strip()
    except ValueError:
        test.cancel("Not find virt-clone command on host.")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    dest_guest_name = params.get("dest_vm", "test-clone")
    dest_guest_file = params.get("dest_image")
    dest_guest_path = None

    # Destroy and undefine the test-clone guest before
    # executing virt-clone command.
    virsh.remove_domain(dest_guest_name)

    cmd = "%s --connect=%s -o %s -n %s" % (VIRT_CLONE,
                                           vm.connect_uri,
                                           vm_name,
                                           dest_guest_name)

    domblklist_result = virsh.domblklist(vm_name)

    if len(domblklist_result.stdout.strip().splitlines()) >= 3:
        # We need a file for destination if guest has block devices.
        dest_guest_path = os.path.join(data_dir.get_data_dir(),
                                       dest_guest_file)
        if os.path.exists(dest_guest_path):
            os.remove(dest_guest_path)

        cmd = "%s -f %s" % (cmd, dest_guest_path)

    try:
        cmd_result = process.run(cmd, ignore_status=True, shell=True)

        if cmd_result.exit_status:
            test.fail("command of virt-clone failed.\n"
                      "output: %s." % cmd_result)

        start_result = None
        # We will get an error of "error: monitor socket did not show up:"
        # when start vm immediately after virt-clone.

        def _start_success():
            start_result = virsh.start(dest_guest_name)
            if start_result.exit_status:
                return False
            return True

        if not utils_misc.wait_for(_start_success, timeout=5):
            test.fail("command virt-clone exit successfully.\n"
                      "but start it failed.\n Detail: %s." %
                      start_result)
    finally:
        # cleanup remove the dest guest.
        virsh.remove_domain(dest_guest_name)
        # remove image file if we created it.
        if dest_guest_path and os.path.exists(dest_guest_path):
            os.remove(dest_guest_path)
