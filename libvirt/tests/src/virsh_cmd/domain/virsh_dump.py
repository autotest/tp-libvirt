import os
import logging
import multiprocessing
import time
import platform
import re

from avocado.utils import process

from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_config
from virttest import data_dir
from virttest import utils_package
from virttest.libvirt_xml import vm_xml


from provider import libvirt_version


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
    """

    vm_name = params.get("main_vm", "vm1")
    vm = env.get_vm(vm_name)
    options = params.get("dump_options")
    dump_file = params.get("dump_file", "vm.core")
    dump_dir = params.get("dump_dir", data_dir.get_tmp_dir())
    if os.path.dirname(dump_file) is "":
        dump_file = os.path.join(dump_dir, dump_file)
    dump_image_format = params.get("dump_image_format")
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
            ret = process.run(cmd2, allow_output_check='combined', shell=True)
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
        the file shoule be normal raw file, otherwise it shoud be compress to
        specified format, the supported compress format including: lzop, gzip,
        bzip2, and xz.
        For memory-only dump, the default dump format is ELF, and it can also
        specify format by --format option, the result could be 'elf' or 'data'.
        """

        valid_format = ["lzop", "gzip", "bzip2", "xz", 'elf', 'data']
        if len(dump_image_format) == 0 or dump_image_format not in valid_format:
            logging.debug("No need check the dumped file format")
            return True
        else:
            file_cmd = "file %s" % dump_file
            ret = process.run(file_cmd, allow_output_check='combined', shell=True)
            status, output = ret.exit_status, ret.stdout_text.strip()
            if status:
                logging.error("Fail to check dumped file %s", dump_file)
                return False
            logging.debug("Run file %s output: %s", dump_file, output)
            actual_format = output.split(" ")[1]
            if actual_format.lower() != dump_image_format.lower():
                logging.error("Compress dumped file to %s fail: %s" %
                              (dump_image_format, actual_format))
                return False
            else:
                return True

    # Configure dump_image_format in /etc/libvirt/qemu.conf.
    qemu_config = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()

    # Install lsof pkg if not installed
    if not utils_package.package_install("lsof"):
        test.cancel("Failed to install lsof in host\n")

    if len(dump_image_format):
        qemu_config.dump_image_format = dump_image_format
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
    finally:
        backup_xml.sync()
        qemu_config.restore()
        libvirtd.restart()
        if os.path.isfile(dump_file):
            os.remove(dump_file)
