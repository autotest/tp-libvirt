import os
import logging
import re
import aexpect
import time
import shutil

from avocado.utils import path as utils_path
from avocado.utils import process

from virttest import utils_libguestfs
from virttest import data_dir
from virttest import utils_misc
from virttest import virsh
from virttest import utils_config
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.staging import service


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
        tailed_log_file = os.path.join(data_dir.get_tmp_dir(), 'tail_log')
        tailed_messages = aexpect.Tail(command='tail -f /var/log/messages',
                                       output_func=utils_misc.log_line,
                                       output_params=(tailed_log_file))
        return tailed_messages

    def chk_on_shutdown(status_error, on_shutdown, parallel_shutdown, output):
        """
        check domains' state when host shutdown, and if parallel_shutdown is set
        to non-zero, check whether the guests have been shutdown corretly.

        :param status_error: positive test if status_error is "no", otherwise
                             negative test
        :param on_shutdown: action taking on host shutdown
        :param parallel_shutdown: the number of parallel_shutdown guests would
                                  be shutdown concurrently on shutdown
        :param output: apppended message from /var/log/messages
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
            if not re.search(expect_msg[dom.name], output):
                logging.debug("expect_mesg is: %s", expect_msg[dom.name])
                if status_error == "no":
                    test.fail("guest should be %s on shutdown" % on_shutdown)
                else:
                    test.fail("Shutdown of guest should be failed to "
                              "complete in time")

        if (on_shutdown == "shutdown") and int(parallel_shutdown) > 0:
            chk_parallel_shutdown(output, parallel_shutdown)

    def chk_on_boot(status_error, on_boot):
        """
        check domains' state when host booted

        :param status_error: positive test if status_error is "no", otherwise
                             negative test
        :param on_boot: action taking on host booting
        """
        if status_error == "no":
            if on_boot == "start":
                for dom in vms:
                    if not dom.is_alive():
                        test.fail("Since on_boot is setting to 'start', "
                                  "guest should be running after "
                                  "restarting libvirt-guests.")
            else:
                for dom in vms:
                    if dom.is_alive():
                        test.fail("Since on_boot is setting to 'ignore', "
                                  "unless guests are autostart, "
                                  "guest should be shut off after "
                                  "restarting libvirt-guests, ")

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
        pattern = r".+ libvirt-guests.sh.*: Starting shutdown on guest: .+"
        shut_start_line_nums = []
        for line_num, line in enumerate(output.splitlines()):
            if re.search(pattern, line):
                shut_start_line_nums.append(line_num)
        logging.debug("the line_numbers contains shutdown messages is: %s ",
                      shut_start_line_nums)

        pattern = r".+ libvirt-guests.sh.*: Shutdown of guest.+complete"
        for line_num, line in enumerate(output.splitlines()):
            if re.search(pattern, line):
                shut_complete_first_line = line_num
                break
        logging.debug("the first line contains shutdown complete messages is: %s ",
                      shut_complete_first_line)

        para_shut = int(parallel_shutdown)
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
                                            "Shutdown of guest %s "
                                            "complete" % dom.name)
                else:
                    expect_msg[dom.name] = ("libvirt-guests.sh.*: "
                                            "Suspending %s: done"
                                            % dom.name)
            else:
                # Now the negative tests are only about ON_SHUTDOWN=shutdown.
                if on_shutdown == "shutdown":
                    expect_msg[dom.name] = ("libvirt-guests.sh.*: "
                                            "Shutdown of guest %s "
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
                else:
                    for dom in vms:
                        if not os.path.exists(save_files[dom]):
                            test.fail("Guests are suspended on host shutdown, "
                                      "and been ignored on host boot, there "
                                      "should be save files for the guests.")

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
        nfs_server = libvirt.setup_or_cleanup_nfs(True)
        nfs_export_dir = nfs_server['export_dir']
        logging.debug("nfs dir is %s", nfs_export_dir)

        # change the orignal xml and image path
        for dom in vms:
            vmxml = vm_xml.VMXML.new_from_dumpxml(dom.name)
            vmxml_backup.append(vmxml.copy())
            disk_xml = vmxml.get_devices(device_type="disk")[0]
            orig_image_path_name = disk_xml.source.attrs['file']

            libvirt.update_vm_disk_source(dom.name, nfs_export_dir)
            shutil.copy(orig_image_path_name, nfs_export_dir)

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

    main_vm_name = params.get("main_vm")
    main_vm = env.get_vm(main_vm_name)

    on_boot = params.get("on_boot")
    on_shutdown = params.get("on_shutdown")
    nfs_vol = params.get("nfs_vol") == "yes"
    virt_use_nfs = params.get("virt_use_nfs") == "on"
    parallel_shutdown = params.get("parallel_shutdown")
    additional_vms = int(params.get("additional_vms", "0"))
    status_error = params.get("status_error")
    shutdown_timeout = params.get("shutdown_timeout", "300")

    config = utils_config.LibvirtGuestsConfig()
    libvirt_guests_service = service.Factory.create_service("libvirt-guests")
    if not libvirt_guests_service.status():
        libvirt_guests_service.start()

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
        dom.wait_for_login()
    first_boot_time = []
    if on_shutdown == "shutdown" and on_boot == "start":
        first_boot_time = boot_time()
        logging.debug("The first boot time is %s", first_boot_time)

    try:
        # Config the libvirt-guests file
        config.ON_BOOT = on_boot
        config.ON_SHUTDOWN = on_shutdown
        config.PARALLEL_SHUTDOWN = parallel_shutdown
        config.SHUTDOWN_TIMEOUT = shutdown_timeout
        process.run("sed -i -e 's/ = /=/g' "
                    "/etc/sysconfig/libvirt-guests",
                    shell=True)

        tail_messages = get_log()
        # Even though libvirt-guests was designed to operate guests when
        # host shutdown. The purpose can also be fullfilled by restart the
        # libvirt-guests service.
        libvirt_guests_service.restart()
        time.sleep(10)
        output = tail_messages.get_output()
        logging.debug("Get messages in /var/log/messages: %s" % output)

        # check the guests state when host shutdown
        chk_on_shutdown(status_error, on_shutdown, parallel_shutdown, output)
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
            virsh.remove_domain(dom.name, "--remove-all-storage")

        if nfs_vol:
            cleanup_nfs_backend_guest(vmxml_backup)

        if libvirt_guests_service.status():
            libvirt_guests_service.stop()
