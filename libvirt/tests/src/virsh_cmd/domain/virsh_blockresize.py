import os
import logging
import commands
from autotest.client.shared import error
from virttest import virsh, data_dir, utils_misc


OVER_SIZE = (1 << 64)


def run(test, params, env):
    """
    Test virsh blockresize command for block device of domain.

    1) Init the variables from params.
    2) Create an image with specified format.
    3) Attach a disk image to vm.
    4) Test blockresize for the disk
    5) Detach the disk
    """

    # MAIN TEST CODE ###
    # Process cartesian parameters
    vm_name = params.get("main_vm", "virt-tests-vm1")
    image_format = params.get("disk_image_format", "qcow2")
    initial_disk_size = params.get("initial_disk_size", "1M")
    status_error = "yes" == params.get("status_error", "yes")
    resize_value = params.get("resize_value")
    virsh_dargs = {'debug': True}

    # Create an image.
    tmp_dir = data_dir.get_tmp_dir()
    image_path = os.path.join(tmp_dir, "blockresize_test")
    logging.info("Create image: %s, "
                 "size %s, "
                 "format %s", image_path, initial_disk_size, image_format)

    cmd = "qemu-img create -f %s %s %s" % (image_format, image_path,
                                           initial_disk_size)
    status, output = commands.getstatusoutput(cmd)
    if status:
        raise error.TestError("Creating image file %s failed: %s"
                              % (image_path, output))

    # Hotplug the image as disk device
    result = virsh.attach_disk(vm_name, source=image_path, target="vdd",
                               extra=" --subdriver %s" % image_format)
    if result.exit_status:
        raise error.TestError("Failed to attach disk %s to VM: %s."
                              % (image_path, result.stderr))

    if resize_value == "over_size":
        # Use byte unit for over_size test
        resize_value = "%s" % OVER_SIZE + "b"

    # Run the test
    try:
        result = virsh.blockresize(vm_name, image_path,
                                   resize_value, **virsh_dargs)
        status = result.exit_status
        err = result.stderr.strip()

        # Check status_error
        if status_error:
            if status == 0 or err == "":
                raise error.TestFail("Expect failure, but run successfully!")
            # No need to do more test
            return
        else:
            if status != 0 or err != "":
                raise error.TestFail("Run failed with right "
                                     "virsh blockresize command")

        if resize_value[-1] in "bkm":
            expected_size = 1024*1024
        elif resize_value[-1] == "g":
            expected_size = 1024*1024*1024
        else:
            raise error.TestError("Unknown infomation of unit")

        image_info = utils_misc.get_image_info(image_path)
        actual_size = int(image_info['vsize'])

        logging.info("The expected block size is %s bytes,"
                     "the actual block size is %s bytes",
                     expected_size, actual_size)

        if int(actual_size) != int(expected_size):
            raise error.TestFail("New blocksize set by blockresize is "
                                 "different from actual size from "
                                 "'qemu-img info'")
    finally:
        virsh.detach_disk(vm_name, target="vdd")

        if os.path.exists(image_path):
            os.remove(image_path)
