import os
import re
import logging

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import utils_misc
from virttest import libvirt_storage
from virttest import libvirt_version


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
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    image_format = params.get("disk_image_format", "qcow2")
    initial_disk_size = params.get("initial_disk_size", "500K")
    status_error = "yes" == params.get("status_error", "yes")
    resize_value = params.get("resize_value")
    virsh_dargs = {'debug': True}

    # Skip 'qed' cases for libvirt version greater than 1.1.0
    if libvirt_version.version_compare(1, 1, 0):
        if image_format == "qed":
            test.cancel("QED support changed, check bug: "
                        "https://bugzilla.redhat.com/show_bug.cgi"
                        "?id=731570")

    # Create an image.
    tmp_dir = data_dir.get_tmp_dir()
    image_path = os.path.join(tmp_dir, "blockresize_test")
    logging.info("Create image: %s, "
                 "size %s, "
                 "format %s", image_path, initial_disk_size, image_format)

    cmd = "qemu-img create -f %s %s %s" % (image_format, image_path,
                                           initial_disk_size)
    ret = process.run(cmd, allow_output_check='combined', shell=True)
    status, output = (ret.exit_status, ret.stdout_text.strip())
    if status:
        test.error("Creating image file %s failed: %s"
                   % (image_path, output))

    # Hotplug the image as disk device
    result = virsh.attach_disk(vm_name, source=image_path, target="vdd",
                               extra=" --subdriver %s" % image_format,
                               **virsh_dargs)
    if result.exit_status:
        test.error("Failed to attach disk %s to VM: %s."
                   % (image_path, result.stderr.strip()))

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
            #if Qemu version > 2.11, zero_size shrink can be supported.
            qemu_version = utils_misc.get_qemu_version()
            is_rhev_installed = qemu_version['is_rhev']
            zero_size_hit = (resize_value == "0"
                             and utils_misc.compare_qemu_version(2, 11, 0, is_rhev=is_rhev_installed))
            if (status == 0 or err == "") and (not zero_size_hit):
                test.fail("Expect failure, but run successfully!")
            # No need to do more test
            return
        else:
            if status != 0 or err != "":
                # bz 1002813 will result in an error on this
                err_str = "unable to execute QEMU command 'block_resize': Could not resize: Invalid argument"
                if resize_value[-2] in "kb" and re.search(err_str, err):
                    test.cancel("BZ 1002813 not yet applied")
                else:
                    test.fail("Run failed with right "
                              "virsh blockresize command")

        # Although kb should not be used, libvirt/virsh will accept it and
        # consider it as a 1000 bytes, which caused issues for qed & qcow2
        # since they expect a value evenly divisible by 512 (hence bz 1002813).
        if "kb" in resize_value:
            value = int(resize_value[:-2])
            if image_format in ["qed", "qcow2"]:
                # qcow2 and qed want a VIR_ROUND_UP value based on 512 byte
                # sectors - hence this less than visually appealing formula
                expected_size = (((value * 1000) + 512 - 1) // 512) * 512
            else:
                # Raw images...
                # Ugh - there's some rather ugly looking math when kb
                # (or mb, gb, tb, etc.) are used as the scale for the
                # value to create an image. The blockresize for the
                # running VM uses a qemu json call which differs from
                # qemu-img would do - resulting in (to say the least)
                # awkward sizes. We'll just have to make sure we don't
                # deviates more than a sector.
                expected_size = value * 1000
        elif "kib" in resize_value:
            value = int(resize_value[:-3])
            expected_size = value * 1024
        elif resize_value[-1] in "b":
            expected_size = int(resize_value[:-1])
        elif resize_value[-1] in "k":
            value = int(resize_value[:-1])
            expected_size = value * 1024
        elif resize_value[-1] == "m":
            value = int(resize_value[:-1])
            expected_size = value * 1024 * 1024
        elif resize_value[-1] == "g":
            value = int(resize_value[:-1])
            expected_size = value * 1024 * 1024 * 1024
            cmd = "qemu-img info %s" % image_path
            if libvirt_storage.check_qemu_image_lock_support():
                cmd += " -U"
            ret = process.run(cmd, allow_output_check='combined', shell=True)
            status, output = (ret.exit_status, ret.stdout_text.strip())
            value_return_by_qemu_img = re.search(r'virtual size:\s+(\d+(\.\d+)?)+\s?G', output).group(1)
            if value != int(float(value_return_by_qemu_img)):
                test.fail("initial image size in config is not equals to value returned by qemu-img info")
        else:
            test.error("Unknown scale value")

        image_info = utils_misc.get_image_info(image_path)
        actual_size = int(image_info['vsize'])

        logging.info("The expected block size is %s bytes, "
                     "the actual block size is %s bytes",
                     expected_size, actual_size)

        # See comment above regarding Raw images
        if image_format == "raw" and resize_value[-2] in "kb":
            if abs(int(actual_size) - int(expected_size)) > 512:
                test.fail("New raw blocksize set by blockresize do "
                          "not match the expected value")
        else:
            if int(actual_size) != int(expected_size):
                test.fail("New blocksize set by blockresize is "
                          "different from actual size from "
                          "'qemu-img info'")
    finally:
        virsh.detach_disk(vm_name, target="vdd", **virsh_dargs)
