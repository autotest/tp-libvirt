import os
import logging as log
import multiprocessing
import time
import platform
import re
import pexpect

from avocado.utils import process
from avocado.utils.software_manager.manager import SoftwareManager
from avocado.utils import distro

from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_config
from virttest import data_dir
from virttest import utils_misc
from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt


from provider import libvirt_version


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test command: virsh dump.

    This command can dump the core of a domain to a file for analysis.
    1. Positive testing
        1.1 Dump domain with valid options.
        1.2 Avoid file system cache when dumping.
        1.3 Compress the dump images to valid/invalid formats.
    2. Negative testing
        2.1 Dump domain to a non-exist directory.
        2.2 Dump domain with invalid option.
        2.3 Dump a shut-off domain.
        2.4 Dump onto directory that's too small.
    """

    vm_name = params.get("main_vm", "vm1")
    vm = env.get_vm(vm_name)
    options = params.get("dump_options")
    dump_file = params.get("dump_file", "vm.core")
    dump_dir = params.get("dump_dir", data_dir.get_tmp_dir())
    if os.path.dirname(dump_file) is "":
        dump_file = os.path.join(dump_dir, dump_file)
    dump_image_format = params.get("dump_image_format")
    small_img = os.path.join(data_dir.get_tmp_dir(), "small.img")
    start_vm = params.get("start_vm") == "yes"
    paused_after_start_vm = params.get("paused_after_start_vm") == "yes"
    status_error = params.get("status_error", "no") == "yes"
    check_bypass_timeout = int(params.get("check_bypass_timeout", "120"))
    memory_dump_format = params.get("memory_dump_format", "")
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'
    document_string = eval(params.get("document_string", "[]"))
    valid_format = ["lzop", "gzip", "bzip2", "xz", 'elf', 'data']
    backup_xml = None

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    def check_flag(file_flags):
        """
        Check if file flag include O_DIRECT.

        :param file_flags: The flags of dumped file

        Note, O_DIRECT(direct disk access hint) is defined as:
        on x86_64:
        #define O_DIRECT        00040000
        on ppc64le or arch64:
        #define O_DIRECT        00200000
        """
        arch = platform.machine()
        file_flag_check = int('00040000', 16)
        if 'ppc64' in arch or 'aarch64' in arch:
            file_flag_check = int('00200000', 16)

        if int(file_flags, 16) & file_flag_check == file_flag_check:
            logging.info("File flags include O_DIRECT")
            return True
        else:
            logging.error("File flags doesn't include O_DIRECT")
            return False

    def check_bypass(dump_file, result_dict):
        """
        Get the file flags of domain core dump file and check it.
        """
        error = ''
        cmd1 = "lsof -w %s |awk '/libvirt_i/{print $2}'" % dump_file
        while True:
            if not os.path.exists(dump_file):
                time.sleep(0.05)
                continue
            ret = process.run(cmd1, shell=True)
            status, output = ret.exit_status, ret.stdout_text.strip()
            if status:
                time.sleep(0.05)
                continue
            cmd2 = "cat /proc/%s/fdinfo/1 |grep flags|awk '{print $NF}'" % output
            ret = process.run(cmd2, shell=True)
            status, output = ret.exit_status, ret.stdout_text.strip()
            if status:
                error = "Fail to get the flags of dumped file"
                logging.error(error)
                break
            if not len(output):
                continue
            try:
                logging.debug("The flag of dumped file: %s", output)
                if check_flag(output):
                    logging.info("Bypass file system cache "
                                 "successfully when dumping")
                    break
                else:
                    error = "Bypass file system cache fail when dumping"
                    logging.error(error)
                    break
            except (ValueError, IndexError) as detail:
                error = detail
                logging.error(error)
                break
        result_dict['bypass'] = error

    def check_domstate(actual, options):
        """
        Check the domain status according to dump options.
        """

        logging.debug("Actual VM status is %s", actual)
        if options.find('live') >= 0:
            domstate = "running"
            if options.find('crash') >= 0 or options.find('reset') > 0:
                domstate = "running"
            if paused_after_start_vm:
                domstate = "paused"
        elif options.find('crash') >= 0:
            domstate = "shut off"
            if options.find('reset') >= 0:
                domstate = "running"
        elif options.find('reset') >= 0:
            domstate = "running"
            if paused_after_start_vm:
                domstate = "paused"
        else:
            domstate = "running"
            if paused_after_start_vm:
                domstate = "paused"

        if not start_vm:
            domstate = "shut off"

        logging.debug("Domain should %s after run dump %s", domstate, options)

        return (domstate == actual)

    def check_dump_format(dump_image_format, dump_file):
        """
        Check the format of dumped file.

        If 'dump_image_format' is not specified or invalid in qemu.conf, then
        the file should be normal raw file, otherwise it should be compress to
        specified format, the supported compress format including: lzop, gzip,
        bzip2, and xz.
        For memory-only dump, the default dump format is ELF, and it can also
        specify format by --format option, the result could be 'elf' or 'data'.
        """

        if len(dump_image_format) == 0 or dump_image_format not in valid_format:
            logging.debug("No need check the dumped file format")
            return True
        else:
            file_cmd = "file %s" % dump_file
            ret = process.run(file_cmd, shell=True)
            status, output = ret.exit_status, ret.stdout_text.strip()
            if status:
                logging.error("Fail to check dumped file %s", dump_file)
                return False
            logging.debug("Run file %s output: %s", dump_file, output)
            actual_format = output.split(" ")[1]
            if actual_format.lower() not in (dump_image_format.lower(), "flattened"):
                logging.error("Compress dumped file to %s fail: %s" %
                              (dump_image_format, actual_format))
                return False
            else:
                return True

    def crash_utility(dump_file, vm):
        """
        Check the working of crash utility tool to analyse the guest dump

        In order for the function to work, both the guest and the host must
        have the same kernel
        If crash tool or kernel debug libraries are not installed, the function
        returns error
        If crash tool cannot read into the vm-core, the function returns fail
        If crash tool can read the vm-core, the function returns true

        Returns:
            0: Success
            1: Crash command failed
            2: Dependency installation failed or debug kernel not found
            3: Kernel mismatch between host and guest
            4: Guest kernel retrieval failed
            5: Unsupported distribution
            6: virsh dump unsuccessful
        """
        def get_guest_kernel(vm):
            """
                Login into the guest and get guest kernel
            """
            try:
                session = vm.wait_for_login(timeout=240)
            except:
                logging.error("Error Logging into the guest")
                return ""
            guest_kernel = session.cmd("uname -r")
            session.close()
            return guest_kernel

        logging.debug("Crash Utility to Analyse Dump")

        # Get guest and host kernel to verify if it is same
        guest_kernel = get_guest_kernel(vm).strip()
        host_kernel = platform.release().strip()
        if not guest_kernel:
            return 4
        if guest_kernel != host_kernel:
            logging.error("Kernel mismatch")
            logging.error("Host Kernel: %s", host_kernel)
            logging.error("Guest Kernel: %s", guest_kernel)
            return 3

        # Get distro version to check for respective libraries
        smm = SoftwareManager()
        detected_distro = distro.detect()
        distro_name = detected_distro.name.lower()
        upstream_kernel = params.get("upstream_kernel", "no") == "yes"

        # Check for required debug tools and libraries
        if distro_name in ("fedora", "rhel"):
            deps = ["kexec-tools", "elfutils", "crash"]
            if not upstream_kernel:
                deps.append("kernel-debuginfo")
        elif distro_name in ("ubuntu"):
            deps = ["linux-crashdump", "kdump-tools", "crash", "elfutils"]
            if not upstream_kernel:
                deps.append("linux-image-debug")
        elif distro_name in ("suse", "sles"):
            deps = ["elfutils", "crash"]
            if not upstream_kernel:
                deps.append("linux-image-debug")
        else:
            return 5
        for package in deps:
            if not smm.check_installed(package) and not smm.install(package):
                logging.error("Failed to install dependency: %s", package)
                return 2

        # Check if debug kernel is present
        if distro_name in ("fedora", "rhel"):
            vmlinux = "/usr/lib/debug/lib/modules/" + host_kernel + "/vmlinux"
        elif distro_name in ("ubuntu", "suse", "sles"):
            vmlinux = "/usr/lib/debug/boot/vmlinux-" + host_kernel
        else:
            return 5
        if upstream_kernel:
            vmlinux = params.get("upstream_kernel_vmlinux", vmlinux)
        if not os.path.isfile(vmlinux):
            logging.error("vmlinux not found")
            return 2

        # Collect guest vm-core
        try:
            virsh.dump(vm_name, dump_file, options,
                       ignore_status=True, debug=True)
        except:
            return 6

        # Run Crash Utility
        crash_cmd = f"crash {vmlinux} {dump_file}"
        logging.debug("Crash command: %s", crash_cmd)
        try:
            # Spawn the crash command
            child = pexpect.spawn(crash_cmd, timeout=100)
            child.expect("crash> ")
            stdout = child.before.decode('utf-8')
            logging.debug("Crash tool output: %s", stdout.strip())

            # Send the back-trace command and capture output
            child.sendline("bt | head")
            child.expect("crash> ")
            stdout = child.before.decode('utf-8')

            # Check if the back-trace produced output
            if "PID" in stdout or "TASK" in stdout:
                logging.info("Crash tool is working correctly.")
                logging.debug("Crash tool bt output: %s", stdout.strip())
                return 0
            else:
                logging.error("Crash tool did not produce expected output.")
                logging.debug("Crash tool output: %s", stdout.strip())
                return 1

        except pexpect.TIMEOUT:
            logging.error("Crash command timed out.")
            return 1

        except Exception as e:
            logging.error("An error occurred while running the crash command: %s", e)
            return 1

    def check_logfile(image_format):
        """
        Checks if libvirt daemon log contains dump_image_format with substring.

        :param image_format: expected image format in error message
        """
        log_file = params.get("libvirtd_debug_file", "")
        if not log_file:
            log_file = utils_misc.get_path(test.debugdir, "libvirtd.log")
        error = "Invalid dump_image_format.*" + image_format
        return utils_misc.wait_for(lambda: libvirt.check_logfile(error, log_file, ignore_status=True), timeout=30)

    # Configure dump_image_format in /etc/libvirt/qemu.conf.
    qemu_config = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    libvirtd_socket = utils_libvirtd.Libvirtd("libvirtd.socket")

    # Install lsof pkg if not installed
    if not utils_package.package_install("lsof"):
        test.cancel("Failed to install lsof in host\n")

    if len(dump_image_format):
        qemu_config.dump_image_format = dump_image_format
        if dump_image_format not in valid_format:
            libvirtd.restart(wait_for_start=False)
            err_msg_found = check_logfile(dump_image_format)
            qemu_config.restore()
            libvirtd_socket.restart()
            if not err_msg_found:
                test.fail("Cannot find the expected error message in log that indicates invalid image mode")
            else:
                return
        libvirtd.restart()

    # Deal with memory-only dump format
    if len(memory_dump_format):
        # Make sure libvirt support this option
        if virsh.has_command_help_match("dump", "--format") is None:
            test.cancel("Current libvirt version doesn't support"
                        " --format option for dump command")
        # Make sure QEMU support this format
        query_cmd = '{"execute":"query-dump-guest-memory-capability"}'
        qemu_capa = virsh.qemu_monitor_command(vm_name, query_cmd).stdout
        if (memory_dump_format not in qemu_capa) and not status_error:
            test.cancel("Unsupported dump format '%s' for"
                        " this QEMU binary" % memory_dump_format)
        options += " --format %s" % memory_dump_format
        if memory_dump_format == 'elf':
            dump_image_format = 'elf'
        if memory_dump_format in ['kdump-zlib', 'kdump-lzo', 'kdump-snappy']:
            dump_image_format = 'data'

    # Back up xml file
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    # check the explanation of "--memory-only" option in virsh dump man.
    if document_string:
        logging.info("document string: %s" % document_string)
        ret = process.run("man virsh", shell=True)
        if ret.exit_status:
            test.error("failed to run 'man virsh'.")
        man_str = re.sub(r"\s+", " ", ret.stdout_text.strip())
        logging.debug("man str: %s" % man_str)
        if not all([item in man_str for item in document_string]):
            test.fail("failed to check document string in virsh man page.")
        logging.info("the document string in virsh man page.")
        return
    dump_guest_core = params.get("dump_guest_core", "")
    if dump_guest_core not in ["", "on", "off"]:
        test.error("invalid dumpCore value: %s" % dump_guest_core)
    try:
        # Set dumpCore in guest xml
        if dump_guest_core:
            if vm.is_alive():
                vm.destroy(gracefully=False)
            vmxml.dumpcore = dump_guest_core
            vmxml.sync()
            vm.start()
            # check qemu-kvm cmdline
            vm_pid = vm.get_pid()
            cmd = "cat /proc/%d/cmdline|xargs -0 echo" % vm_pid
            cmd += "|grep dump-guest-core=%s" % dump_guest_core
            result = process.run(cmd, ignore_status=True, shell=True)
            logging.debug("cmdline: %s" % result.stdout_text)
            if result.exit_status:
                test.fail("Not find dump-guest-core=%s in qemu cmdline"
                          % dump_guest_core)
            else:
                logging.info("Find dump-guest-core=%s in qemum cmdline",
                             dump_guest_core)

        # Deal with bypass-cache option
        if options.find('bypass-cache') >= 0:
            vm.wait_for_login()
            result_dict = multiprocessing.Manager().dict()
            child_process = multiprocessing.Process(target=check_bypass,
                                                    args=(dump_file, result_dict))
            child_process.start()

        # Create too small a directory
        if "too_small" in dump_dir:
            libvirt_disk.create_disk("file",
                                     small_img,
                                     "100M",
                                     "raw")
            libvirt.mkfs(small_img, "ext3")
            os.mkdir(dump_dir)
            utils_misc.mount(small_img, dump_dir, None)

        # Check for Crash Utility test
        crash_utility_test = params.get("crash_utility", "no") == "yes"

        if not crash_utility_test:
            # Run virsh command
            cmd_result = virsh.dump(vm_name, dump_file, options,
                                    unprivileged_user=unprivileged_user,
                                    uri=uri,
                                    ignore_status=True, debug=True)
            status = cmd_result.exit_status
            if 'child_process' in locals():
                child_process.join(timeout=check_bypass_timeout)
                params['bypass'] = result_dict['bypass']

            logging.info("Start check result")
            time.sleep(5)
            if not check_domstate(vm.state(), options):
                test.fail("Domain status check fail.")
            if status_error:
                if not status:
                    test.fail("Expect fail, but run successfully")
            else:
                if status:
                    test.fail("Expect succeed, but run fail")
                if not os.path.exists(dump_file):
                    test.fail("Fail to find domain dumped file.")
                if check_dump_format(dump_image_format, dump_file):
                    logging.info("Successfully dump domain to %s", dump_file)
                else:
                    test.fail("The format of dumped file is wrong.")
            if params.get('bypass'):
                test.fail(params['bypass'])

        else:
            crash_tool = crash_utility(dump_file, vm)
            if crash_tool == 6:
                test.fail("Unable to collect guest vmcore")
            if crash_tool == 5:
                test.cancel("Test unsupported for distro")
            if crash_tool == 4:
                test.error("Guest login issue. Unable to get guest kernel version")
            if crash_tool == 3:
                test.cancel("Guest and Host kernel are different")
            elif crash_tool == 2:
                test.error("Required debug libraries/tools not installed")
            elif crash_tool == 1:
                test.fail("Unable to analyse guest vmcore using crash")
            else:
                logging.info("Able to analyse guest vmcore using crash")

    finally:
        if backup_xml:
            backup_xml.sync()
        qemu_config.restore()
        libvirtd.restart()
        if os.path.isfile(small_img):
            # small_img is a file and will show as /dev/loop0 in /proc/mounts
            utils_misc.umount("/dev/loop0", dump_dir, None, verbose=True)
            os.rmdir(dump_dir)
            os.remove(small_img)
        if os.path.isfile(dump_file):
            os.remove(dump_file)
