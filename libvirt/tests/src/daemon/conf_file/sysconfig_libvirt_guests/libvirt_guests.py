import os
import logging as log
import re
import aexpect
import time
import shutil

from avocado.utils import path as utils_path
from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_libguestfs
from virttest import utils_misc
from virttest import virsh
from virttest import utils_config
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk
from virttest.staging import service


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Configuring /etc/sysconfig/libvirt-guests, then check the domains
    status after restarting the libvirt-guests.server,

    1. Set the values in /etc/sysconfig/libvirt-guests
    2. Restart libvirt-guests service
    3. Check the domains states, and the guests' save files
    """

    def get_log():
        """
        Tail output appended data as the file /var/log/messages grows

        :returns: the appended data tailed from /var/log/messages
        """
        tailed_messages = aexpect.Tail(command='tail -f /var/log/messages')
        logging.debug("Tail of log messages are logged in %s",
                      tailed_messages.output_filename)
        return tailed_messages

    def chk_on_shutdown(status_error, on_shutdown, parallel_shutdown, output):
        """
        check domains' state when host shutdown, and if parallel_shutdown is set
        to non-zero, check whether the guests have been shutdown correctly.

        :param status_error: positive test if status_error is "no", otherwise
                             negative test
        :param on_shutdown: action taking on host shutdown
        :param parallel_shutdown: the number of parallel_shutdown guests would
                                  be shutdown concurrently on shutdown
        :param output: appended message from /var/log/messages
        """
        if on_shutdown == "shutdown" and on_boot == "start":
            second_boot_time = boot_time()
            logging.debug("The second boot time is %s", second_boot_time)
            if any([i >= j for i, j in zip(first_boot_time, second_boot_time)]):
                test.fail("The second boot time for should be larger"
                          "than its first boot time.")

        expect_msg = expect_shutdown_msg(status_error, on_shutdown)
        logging.debug("The expected messages when host shutdown is: %s ", expect_msg)
        for dom in vms:
            check_res = re.search(expect_msg[dom.name], output)
            no_operation = (transient_vm_operation == "nothing" and not dom.is_persistent())
            if no_operation:
                if check_res:
                    test.fail("Expect msg '%s' does not exist." % expect_msg)
                test.log.debug("Check transient guest:%s did nothing successfully", dom.name)
            else:
                if not check_res:
                    logging.debug("expect_mesg is: %s", expect_msg[dom.name])
                    if status_error == "no":
                        test.fail("guest:%s should be %s on shutdown" % (dom.name, on_shutdown))
                    else:
                        test.fail("Shutdown of guest should be failed to "
                                  "complete in time")
                test.log.debug("Check guest:%s did '%s' successfully",
                               dom.name, expect_msg[dom.name])

        if (on_shutdown == "shutdown") and int(parallel_shutdown) > 0:
            chk_parallel_shutdown(output, parallel_shutdown)

    def chk_on_boot(status_error, on_boot):
        """
        check domains' state when host booted

        :param status_error: positive test if status_error is "no", otherwise
                             negative test
        :param on_boot: action taking on host booting which set in the conf file
        """
        if status_error == "no":
            if on_boot != "ignore":
                for dom in active_persistent_vms:
                    if not dom.is_alive():
                        test.fail("guest:%s should be running after "
                                  "restarting libvirt-guests." % dom.name)
            else:
                for dom in vms:
                    if dom.is_alive():
                        test.fail("Since on_boot is setting to 'ignore', "
                                  "unless guests are autostart, "
                                  "guest should be shut off after "
                                  "restarting libvirt-guests, ")
        test.log.debug("Check all guests state successfully")

    def check_on_shutdown_vm_status():
        for dom in vms:
            result = virsh.domstate(dom.name, "--reason")
            try:
                dom.wait_for_shutdown()
            except Exception as e:
                test.fail('As on_boot is set to "ignore", but guest %s is '
                          'not shutdown. reason: %s ' % (dom.name, e))

    def chk_parallel_shutdown(output, parallel_shutdown):
        """
        check whether the guests has been shut down concurrently
        on host shutdown.
        """
        pattern = r".+ libvirt-guests.sh.*: .*tarting shutdown on guest: .+"
        shut_start_line_nums = []
        for line_num, line in enumerate(output.splitlines()):
            if re.search(pattern, line):
                shut_start_line_nums.append(line_num)
        logging.debug("the line_numbers contains shutdown messages is: %s ",
                      shut_start_line_nums)

        pattern = r".+ libvirt-guests.sh.*: .*hutdown of guest.+complete"
        for line_num, line in enumerate(output.splitlines()):
            if re.search(pattern, line):
                shut_complete_first_line = line_num
                break
        logging.debug("the first line contains shutdown complete messages is: %s ",
                      shut_complete_first_line)

        para_shut = int(parallel_shutdown)
        logging.debug('shut_start_line_nums: %s', shut_start_line_nums)
        if len(shut_start_line_nums) <= para_shut:
            test.error('Did not get expected output. What we have is: %s' %
                       shut_start_line_nums)
        if shut_start_line_nums[para_shut-1] > shut_complete_first_line:
            test.fail("Since parallel_shutdown is setting to non_zero, "
                      "%s guests should be shutdown concurrently."
                      % parallel_shutdown)
        if shut_start_line_nums[para_shut] < shut_complete_first_line:
            test.fail("The number of guests shutdown concurrently "
                      "should not be exceeded than %s."
                      % parallel_shutdown)

    def expect_shutdown_msg(status_error, on_shutdown):
        """
        the expected messages of each guests when host shutdown
        logged into /var/log/messages
        """
        expect_msg = {}
        for dom in vms:
            if status_error == "no":
                if on_shutdown == "shutdown":
                    expect_msg[dom.name] = ("libvirt-guests.sh.*: "
                                            ".*hutdown of guest %s "
                                            "complete" % dom.name)
                else:
                    expect_msg[dom.name] = ("libvirt-guests.sh.*: "
                                            "Suspending %s: done"
                                            % dom.name)
            else:
                # Now the negative tests are only about ON_SHUTDOWN=shutdown.
                if on_shutdown == "shutdown":
                    expect_msg[dom.name] = ("libvirt-guests.sh.*: "
                                            ".*hutdown of guest %s "
                                            "failed to complete in "
                                            "time" % dom.name)
        return expect_msg

    def chk_save_files(status_error, on_shutdown, on_boot):
        """
        save files should exist when on_shutdown is set to shutdown, and
        on_boot is set to ignore. In other conditions, there should be
        no save files.
        """
        save_files = dict()
        for dom in vms:
            save_files[dom] = ("/var/lib/libvirt/qemu/save/%s.save" %
                               dom.name)
        if status_error == "no":
            if on_shutdown == "shutdown":
                for dom in vms:
                    if os.path.exists(save_files[dom]):
                        test.fail("There should be no save files since "
                                  "guests are shutdown on host shutdown.")
            else:
                if on_boot == "start":
                    for dom in vms:
                        if os.path.exists(save_files[dom]):
                            test.fail("There should be no save files since "
                                      "guests are restored on host shutdown.")
                elif on_boot == "ignore":
                    for dom in vms:
                        if not os.path.exists(save_files[dom]):
                            test.fail("Guests are suspended on host shutdown, "
                                      "and been ignored on host boot, there "
                                      "should be save files for the guests.")
        test.log.debug("Check all guests file successfully")

    def boot_time():
        booting_time = []
        for vm in vms:
            session = vm.wait_for_login()
            time = session.cmd_output("uptime --since")
            booting_time.append(time)
            session.close()
        return booting_time

    def setup_nfs_backend_guest(vmxml_backup):
        # nfs_server setup
        nfs_server = libvirt.setup_or_cleanup_nfs(True, is_mount=True)
        nfs_mount_dir = nfs_server['mount_dir']
        logging.debug("nfs dir is %s", nfs_mount_dir)

        # change the original xml and image path
        for dom in vms:
            vmxml = vm_xml.VMXML.new_from_dumpxml(dom.name)
            vmxml_backup.append(vmxml.copy())
            disk_xml = vmxml.get_devices(device_type="disk")[0]
            orig_image_path_name = disk_xml.source.attrs['file']

            libvirt.update_vm_disk_source(dom.name, nfs_mount_dir)
            shutil.copy(orig_image_path_name, nfs_mount_dir)

        # set the selinux bool
        if virt_use_nfs:
            result = process.run("setsebool virt_use_nfs 1",
                                 shell=True, verbose=True)
            if result.exit_status:
                logging.error("Failed to set virt_use_nfs on")

    def cleanup_nfs_backend_guest(vmxml_backup):
        if virt_use_nfs:
            result = process.run("setsebool virt_use_nfs 0",
                                 shell=True, verbose=True)
            if result.exit_status:
                logging.error("Failed to set virt_use_nfs off")

        # recover the guest xml
        for xml_backup in vmxml_backup:
            xml_backup.sync(options="--managed-save")

        nfs_server = libvirt.setup_or_cleanup_nfs(False)

    def transfer_to_transient(per_guest_name):
        """
        Transfer one persistent vm to transient guest.

        :param  per_guest_name: persistent guest name.
        """
        virsh.start(per_guest_name, ignore_status=False)
        vmxml = vm_xml.VMXML.new_from_dumpxml(per_guest_name)
        virsh.undefine(per_guest_name, options='--nvram', debug=True, ignore_status=False)
        virsh.create(vmxml.xml, debug=True)
        test.log.debug("Transfer guest %s to be transient type", per_guest_name)

    main_vm_name = params.get("main_vm")
    main_vm = env.get_vm(main_vm_name)
    # Back up domain XML before the test
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(main_vm_name)
    vmxml_bakup = vmxml.copy()

    on_boot = params.get("on_boot")
    on_shutdown = params.get("on_shutdown")
    persistent_only = params.get("persistent_only", "")
    transient_vm = "yes" == params.get("transient_vm", "no")
    if transient_vm:
        libvirt_version.is_libvirt_feature_supported(params)
    transient_vm_operation = params.get("transient_vm_operation")
    nfs_vol = params.get("nfs_vol") == "yes"
    virt_use_nfs = params.get("virt_use_nfs") == "on"
    parallel_shutdown = params.get("parallel_shutdown")
    additional_vms = int(params.get("additional_vms", "0"))
    status_error = params.get("status_error")
    shutdown_timeout = params.get("shutdown_timeout", "300")

    # Create libvirt guest config file if not existed
    libvirt_guests_file = "/etc/sysconfig/libvirt-guests"
    libvirt_guests_file_create = False
    if not os.path.exists(libvirt_guests_file):
        process.run("touch %s" % libvirt_guests_file, verbose=True)
        libvirt_guests_file_create = True

    config = utils_config.LibvirtGuestsConfig()
    libvirt_guests_service = service.Factory.create_service("libvirt-guests")
    if (not libvirt_guests_service.status()
            and not libvirt_guests_service.start()):
        process.run("journalctl -u libvirt-guests", ignore_status=True)
        test.error("libvirt-guests service failed to start. Please check logs")

    vms = [main_vm]
    if main_vm.is_alive:
        main_vm.destroy(gracefully=False)

    if not utils_misc.start_rsyslogd():
        test.error("Rsyslogd service start fail")

    try:
        utils_path.find_command("virt-clone")
    except utils_path.CmdNotFoundError:
        test.cancel("No virt-clone command found.")

    # Clone additional vms: avocado-vt-vm2, avocado-vt-vm3.....
    for i in range(additional_vms):
        guest_name = ("%s" % main_vm_name[:-1])+("%s" % str(i+2))
        logging.debug("guest_name : %s", guest_name)
        utils_libguestfs.virt_clone_cmd(main_vm_name, guest_name,
                                        True, timeout=360,
                                        ignore_status=False)
        if transient_vm:
            transfer_to_transient(guest_name)
        vms.append(main_vm.clone(guest_name))
        logging.debug("Now the vms is: %s", [dom.name for dom in vms])
    if nfs_vol:
        # info collected for clear env finally
        vmxml_backup = []
        setup_nfs_backend_guest(vmxml_backup)

    for dom in vms:
        if not dom.is_alive():
            dom.start()
    for dom in vms:
        dom.wait_for_login().close()
    first_boot_time = []
    if on_shutdown == "shutdown" and on_boot == "start":
        first_boot_time = boot_time()
        logging.debug("The first boot time is %s", first_boot_time)

    try:
        # Config the libvirt-guests file
        if on_boot:
            config.ON_BOOT = on_boot
        if on_shutdown:
            config.ON_SHUTDOWN = on_shutdown
        if persistent_only:
            config.PERSISTENT_ONLY = persistent_only
        if parallel_shutdown:
            config.PARALLEL_SHUTDOWN = parallel_shutdown
        if shutdown_timeout:
            config.SHUTDOWN_TIMEOUT = shutdown_timeout
        process.run("sed -i -e 's/ = /=/g' "
                    "/etc/sysconfig/libvirt-guests",
                    shell=True)
        process.run("cat /etc/sysconfig/libvirt-guests", shell=True)

        tail_messages = get_log()
        # Before restart libvirt-guests, check the status of all VMs
        active_persistent_vms = []
        for dom in vms:
            if dom.is_alive and dom.is_persistent:
                active_persistent_vms.append(dom)
        # Even though libvirt-guests was designed to operate guests when
        # host shutdown. The purpose can also be fulfilled by restart the
        # libvirt-guests service.
        if not libvirt_guests_service.restart():
            process.run("journalctl -u libvirt-guests", ignore_status=True)
            test.error("libvirt-guests failed to restart. Please check logs.")
        time.sleep(30)
        output = tail_messages.get_output()
        logging.debug("Get messages in /var/log/messages: %s" % output)
        virsh.dom_list("--all", debug=True)
        # check the guests state when host shutdown
        chk_on_shutdown(status_error, on_shutdown, parallel_shutdown, output)
        virsh.dom_list("--all", debug=True)
        if not (transient_vm and on_shutdown == "shutdown"):
            # check the guests state when host rebooted
            chk_on_boot(status_error, on_boot)
        # check the guests save files
        chk_save_files(status_error, on_shutdown, on_boot)

        if on_boot == "ignore" and on_shutdown == "shutdown":
            check_on_shutdown_vm_status()

    finally:
        config.restore()

        # Undefine additional vms
        for dom in vms[1:]:
            if dom.is_alive():
                dom.destroy(gracefully=False)
            virsh.remove_domain(dom.name, "--remove-all-storage --nvram")

        if libvirt_guests_file_create:
            os.remove(libvirt_guests_file)

        if libvirt_guests_service.status():
            libvirt_guests_service.stop()

        source_path = os.path.dirname(libvirt_disk.get_first_disk_source(vms[0]))
        process.run("cd %s; rm -rf *-clone*" % source_path, shell=True)
        if nfs_vol:
            cleanup_nfs_backend_guest(vmxml_backup)
        vmxml_bakup.sync(options="--managed-save")
