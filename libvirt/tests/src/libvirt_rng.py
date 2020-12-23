import re
import os
import ast
import shutil
import logging
import uuid
import aexpect

from six.moves import xrange

from avocado.utils import process

from virttest import virt_vm, virsh
from virttest import utils_package
from virttest import utils_misc
from virttest import libvirt_version
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.libvirt_xml.devices import rng


def run(test, params, env):
    """
    Test rng device options.

    1.Prepare test environment, destroy or suspend a VM.
    2.Edit xml and start the domain.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    def check_rng_xml(xml_set, exists=True):
        """
        Check rng xml in/not in domain xml
        :param xml_set: rng xml object for setting
        :param exists: Check xml exists or not in domain xml

        :return: boolean
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        # Get all current xml rng devices
        xml_devices = vmxml.devices
        rng_devices = xml_devices.by_device_tag("rng")
        logging.debug("rng_devices is %s", rng_devices)

        # check if xml attr same with checking
        try:
            rng_index = xml_devices.index(rng_devices[0])
            xml_get = xml_devices[rng_index]

            if not exists:
                # should be detach device check
                return False
        except IndexError:
            if exists:
                # should be attach device check
                return False
            else:
                logging.info("Can not find rng xml as expected")
                return True

        def get_compare_values(xml_set, xml_get, rng_attr):
            """
            Get set and get value to compare

            :param xml_set: seting xml object
            :param xml_get: getting xml object
            :param rng_attr: attribute of rng device
            :return: set and get value in xml
            """
            try:
                set_value = xml_set[rng_attr]
            except xcepts.LibvirtXMLNotFoundError:
                set_value = None
            try:
                get_value = xml_get[rng_attr]
            except xcepts.LibvirtXMLNotFoundError:
                get_value = None
            logging.debug("get xml_set value(%s) is %s, get xml_get value is %s",
                          rng_attr, set_value, get_value)
            return (set_value, get_value)

        match = True
        for rng_attr in xml_set.__slots__:
            set_value, get_value = get_compare_values(xml_set, xml_get, rng_attr)
            logging.debug("rng_attr=%s, set_value=%s, get_value=%s", rng_attr, set_value, get_value)
            if set_value and set_value != get_value:
                if rng_attr == 'backend':
                    for bak_attr in xml_set.backend.__slots__:
                        set_backend, get_backend = get_compare_values(xml_set.backend, xml_get.backend, bak_attr)
                        if set_backend and set_backend != get_backend:
                            if bak_attr == 'source':
                                set_source = xml_set.backend.source
                                get_source = xml_get.backend.source
                                find = False
                                for i in range(len(set_source)):
                                    for j in get_source:
                                        if set(set_source[i].items()).issubset(j.items()):
                                            find = True
                                            break
                                    if not find:
                                        logging.debug("set source(%s) not in get source(%s)",
                                                      set_source[i], get_source)
                                        match = False
                                        break
                                    else:
                                        continue
                            else:
                                logging.debug("set backend(%s)- %s not equal to get backend-%s",
                                              rng_attr, set_backend, get_backend)
                                match = False
                                break
                        else:
                            continue
                        if not match:
                            break
                else:
                    logging.debug("set value(%s)-%s not equal to get value-%s",
                                  rng_attr, set_value, get_value)
                    match = False
                    break
            else:
                continue
            if not match:
                break

        if match:
            logging.info("Find same rng xml as hotpluged")
        else:
            test.fail("Rng xml in VM not same with attached xml")

        return True

    def modify_rng_xml(dparams, sync=True, get_xml=False):
        """
        Modify interface xml options

        :params dparams: parameters for organize xml
        :params sync: whether sync to domain xml, if get_xml is True,
                      then sync will not take effect
        :params get_xml: whether get device xml
        :return: if get_xml=True, return xml file
        """
        rng_model = dparams.get("rng_model", "virtio")
        rng_rate = dparams.get("rng_rate")
        backend_model = dparams.get("backend_model", "random")
        backend_type = dparams.get("backend_type")
        backend_dev = dparams.get("backend_dev", "")
        backend_source_list = dparams.get("backend_source",
                                          "").split()
        backend_protocol = dparams.get("backend_protocol")
        rng_alias = dparams.get("rng_alias")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        rng_xml = rng.Rng()
        rng_xml.rng_model = rng_model
        if rng_rate:
            rng_xml.rate = ast.literal_eval(rng_rate)
        backend = rng.Rng.Backend()
        backend.backend_model = backend_model
        if backend_type:
            backend.backend_type = backend_type
        if backend_dev:
            backend.backend_dev = backend_dev
        if backend_source_list:
            source_list = [ast.literal_eval(source) for source in
                           backend_source_list]
            backend.source = source_list
        if backend_protocol:
            backend.backend_protocol = backend_protocol
        rng_xml.backend = backend
        if detach_alias:
            rng_xml.alias = dict(name=rng_alias)
        if with_packed:
            rng_xml.driver = dict(packed=driver_packed)

        logging.debug("Rng xml: %s", rng_xml)
        if get_xml:
            return rng_xml
        if sync:
            vmxml.add_device(rng_xml)
            vmxml.xmltreefile.write()
            vmxml.sync()
        else:
            status = libvirt.exec_virsh_edit(
                vm_name, [(r":/<devices>/s/$/%s" %
                           re.findall(r"<rng.*<\/rng>",
                                      str(rng_xml), re.M
                                      )[0].replace("/", "\/"))])
            if not status:
                test.fail("Failed to edit vm xml")

    def check_qemu_cmd(dparams):
        """
        Verify qemu-kvm command line.
        """
        rng_model = dparams.get("rng_model", "virtio")
        rng_rate = dparams.get("rng_rate")
        backend_type = dparams.get("backend_type")
        backend_model = dparams.get("backend_model")
        backend_source_list = dparams.get("backend_source",
                                          "").split()
        cmd = ("ps -ef | grep %s | grep -v grep" % vm_name)
        chardev = src_host = src_port = None
        if backend_type == "tcp":
            chardev = "socket"
        elif backend_type == "udp":
            chardev = "udp"
        for bc_source in backend_source_list:
            source = ast.literal_eval(bc_source)
            if "mode" in source and source['mode'] == "connect":
                src_host = source['host']
                src_port = source['service']

        if backend_model == "builtin":
            cmd += (" | grep rng-builtin")
        if chardev and src_host and src_port:
            cmd += (" | grep 'chardev %s,.*host=%s,port=%s'"
                    % (chardev, src_host, src_port))
        if rng_model == "virtio":
            cmd += (" | grep 'device %s'" % dparams.get("rng_device"))
        if rng_rate:
            rate = ast.literal_eval(rng_rate)
            cmd += (" | grep 'max-bytes=%s,period=%s'"
                    % (rate['bytes'], rate['period']))
        if with_packed:
            cmd += (" | grep 'packed=%s'" % driver_packed)
        if process.run(cmd, ignore_status=True, shell=True).exit_status:
            test.fail("Can't see rng option"
                      " in command line")

    def check_host():
        """
        Check random device on host
        """
        backend_dev = params.get("backend_dev")
        if backend_dev:
            cmd = "lsof |grep %s" % backend_dev
            ret = process.run(cmd, ignore_status=True, shell=True)
            if ret.exit_status or not ret.stdout_text.count("qemu"):
                test.fail("Failed to check random device"
                          " on host, command output: %s" %
                          ret.stdout_text)

    def check_snapshot(bgjob=None):
        """
        Do snapshot operation and check the results
        """
        snapshot_name1 = "snap.s1"
        snapshot_name2 = "snap.s2"
        if not snapshot_vm_running:
            vm.destroy(gracefully=False)
        ret = virsh.snapshot_create_as(vm_name, snapshot_name1, debug=True)
        libvirt.check_exit_status(ret)
        snap_lists = virsh.snapshot_list(vm_name, debug=True)
        if snapshot_name not in snap_lists:
            test.fail("Snapshot %s doesn't exist"
                      % snapshot_name)

        if snapshot_vm_running:
            options = "--force"
        else:
            options = ""
        ret = virsh.snapshot_revert(
            vm_name, ("%s %s" % (snapshot_name, options)), debug=True)
        libvirt.check_exit_status(ret)
        ret = virsh.dumpxml(vm_name, debug=True)
        if ret.stdout.strip().count("<rng model="):
            test.fail("Found rng device in xml")

        if snapshot_with_rng:
            if vm.is_alive():
                vm.destroy(gracefully=False)
            if bgjob:
                bgjob.kill_func()
            modify_rng_xml(params, False)

        # Start the domain before disk-only snapshot
        if vm.is_dead():
            # Add random server
            if params.get("backend_type") == "tcp":
                cmd = "cat /dev/random | nc -4 -l localhost 1024"
                bgjob = utils_misc.AsyncJob(cmd)
            vm.start()
            vm.wait_for_login().close()
        err_msgs = ("live disk snapshot not supported"
                    " with this QEMU binary")
        ret = virsh.snapshot_create_as(vm_name,
                                       "%s --disk-only"
                                       % snapshot_name2, debug=True)
        if ret.exit_status:
            if ret.stderr.count(err_msgs):
                test.cancel(err_msgs)
            else:
                test.fail("Failed to create external snapshot")
        snap_lists = virsh.snapshot_list(vm_name, debug=True)
        if snapshot_name2 not in snap_lists:
            test.fail("Failed to check snapshot list")

        ret = virsh.domblklist(vm_name, debug=True)
        if not ret.stdout.strip().count(snapshot_name2):
            test.fail("Failed to find snapshot disk")

    def check_guest_dump(session, exists=True):
        """
        Check guest with hexdump

        :param session: ssh session to guest
        :param exists: check rng device exists/not exists
        """
        check_cmd = "hexdump /dev/hwrng -n 100"
        try:
            status = session.cmd_status(check_cmd, 60)

            if status != 0 and exists:
                test.fail("Fail to check hexdump in guest")
            elif not exists:
                logging.info("hexdump cmd failed as expected")
        except aexpect.exceptions.ShellTimeoutError:
            if not exists:
                test.fail("Still can find rng device in guest")
            else:
                logging.info("Hexdump do not fail with error")

    def check_guest(session, expect_fail=False):
        """
        Check random device on guest

        :param session: ssh session to guest
        :param expect_fail: expect the dd cmd pass or fail
        """
        rng_files = (
            "/sys/devices/virtual/misc/hw_random/rng_available",
            "/sys/devices/virtual/misc/hw_random/rng_current")
        rng_avail = session.cmd_output("cat %s" % rng_files[0],
                                       timeout=timeout).strip()
        rng_currt = session.cmd_output("cat %s" % rng_files[1],
                                       timeout=timeout).strip()
        logging.debug("rng avail:%s, current:%s", rng_avail, rng_currt)
        if not rng_currt.count("virtio") or rng_currt not in rng_avail:
            test.fail("Failed to check rng file on guest")

        # Read the random device
        rng_rate = params.get("rng_rate")
        # For rng rate test this command and return in a short time
        # but for other test it will hang
        cmd = ("dd if=/dev/hwrng of=rng.test count=100"
               " && rm -f rng.test")
        try:
            ret, output = session.cmd_status_output(cmd, timeout=timeout)
            if ret and expect_fail:
                logging.info("dd cmd failed as expected")
            elif ret:
                test.fail("Failed to read the random device")
        except aexpect.exceptions.ShellTimeoutError:
            logging.info("dd cmd timeout")
            # Close session as the current session still hang on last cmd
            session.close()
            session = vm.wait_for_login()

            if expect_fail:
                test.fail("Still can find rng device in guest")
            else:
                logging.info("dd cmd do not fail with error")
                # Check if file have data
                size = session.cmd_output("wc -c rng.test").split()[0]
                if int(size) > 0:
                    logging.info("/dev/hwrng is not empty, size %s", size)
                else:
                    test.fail("/dev/hwrng is empty")
        finally:
            session.cmd("rm -f rng.test")

        if rng_rate:
            rate_bytes, rate_period = list(ast.literal_eval(rng_rate).values())
            rate_conf = float(rate_bytes) / (float(rate_period)/1000)
            ret = re.search(r"(\d+) bytes.*copied, (\d+.\d+) s",
                            output, re.M)
            if not ret:
                test.fail("Can't find rate from output")
            rate_real = float(ret.group(1)) / float(ret.group(2))
            logging.debug("Find rate: %s, config rate: %s",
                          rate_real, rate_conf)
            if rate_real > rate_conf * 1.2:
                test.fail("The rate of reading exceed"
                          " the limitation of configuration")
        if device_num > 1:
            rng_dev = rng_avail.split()
            if len(rng_dev) != device_num:
                test.cancel("Multiple virtio-rng devices are not"
                            " supported on this guest kernel. "
                            "Bug: https://bugzilla.redhat.com/"
                            "show_bug.cgi?id=915335")
            session.cmd("echo -n %s > %s" % (rng_dev[1], rng_files[1]))
            # Read the random device
            if session.cmd_status(cmd, timeout=timeout):
                test.fail("Failed to read the random device")

    def get_rng_device(guest_arch, rng_model):
        """
        Return the expected rng device in qemu cmd
        :param guest_arch: e.g. x86_64
        :param rng_model: the value for //rng@model, e.g. "virtio"
        :return: expected device type in qemu cmd
        """
        if "virtio" in rng_model:
            return "virtio-rng-pci" if "s390x" not in guest_arch else "virtio-rng-ccw"
        else:
            test.fail("Unknown rng model %s" % rng_model)

    start_error = "yes" == params.get("start_error", "no")
    status_error = "yes" == params.get("status_error", "no")

    test_host = "yes" == params.get("test_host", "no")
    test_guest = "yes" == params.get("test_guest", "no")
    test_guest_dump = "yes" == params.get("test_guest_dump", "no")
    test_qemu_cmd = "yes" == params.get("test_qemu_cmd", "no")
    test_snapshot = "yes" == params.get("test_snapshot", "no")
    snapshot_vm_running = "yes" == params.get("snapshot_vm_running",
                                              "no")
    snapshot_with_rng = "yes" == params.get("snapshot_with_rng", "no")
    snapshot_name = params.get("snapshot_name")
    device_num = int(params.get("device_num", 1))
    detach_alias = "yes" == params.get("rng_detach_alias", "no")
    detach_alias_options = params.get("rng_detach_alias_options")
    attach_rng = "yes" == params.get("rng_attach_device", "no")
    attach_options = params.get("rng_attach_options", "")
    random_source = "yes" == params.get("rng_random_source", "yes")
    timeout = int(params.get("timeout", 600))
    wait_timeout = int(params.get("wait_timeout", 60))
    with_packed = "yes" == params.get("with_packed", "no")
    driver_packed = params.get("driver_packed", "on")

    if params.get("backend_model") == "builtin" and not libvirt_version.version_compare(6, 2, 0):
        test.cancel("Builtin backend is not supported on this libvirt version")

    if device_num > 1 and not libvirt_version.version_compare(1, 2, 7):
        test.cancel("Multiple virtio-rng devices not "
                    "supported on this libvirt version")

    if with_packed and not libvirt_version.version_compare(6, 3, 0):
        test.cancel("The virtio packed attribute is not supported in"
                    " current libvirt version.")

    guest_arch = params.get("vm_arch_name", "x86_64")

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    logging.debug("vm xml is %s", vmxml_backup)

    # Try to install rng-tools on host, it can speed up random rate
    # if installation failed, ignore the error and continue the test
    if utils_package.package_install(["rng-tools"]):
        rngd_conf = "/etc/sysconfig/rngd"
        rngd_srv = "/usr/lib/systemd/system/rngd.service"
        if os.path.exists(rngd_conf):
            # For rhel6 host, add extraoptions
            with open(rngd_conf, 'w') as f_rng:
                f_rng.write('EXTRAOPTIONS="--rng-device /dev/urandom"')
        elif os.path.exists(rngd_srv):
            # For rhel7 host, modify start options
            rngd_srv_conf = "/etc/systemd/system/rngd.service"
            if not os.path.exists(rngd_srv_conf):
                shutil.copy(rngd_srv, rngd_srv_conf)
            process.run("sed -i -e 's#^ExecStart=.*#ExecStart=/sbin/rngd"
                        " -f -r /dev/urandom -o /dev/random#' %s"
                        % rngd_srv_conf, shell=True)
            process.run('systemctl daemon-reload')
        process.run("service rngd start")

    # Build the xml and run test.
    try:
        bgjob = None

        # Prepare xml, make sure no extra rng dev.
        vmxml = vmxml_backup.copy()
        vmxml.remove_all_device_by_type('rng')
        vmxml.sync()
        logging.debug("Prepared vm xml without rng dev is %s", vmxml)

        # Take snapshot if needed
        if snapshot_name:
            if snapshot_vm_running:
                vm.start()
                vm.wait_for_login().close()
            ret = virsh.snapshot_create_as(vm_name, snapshot_name, debug=True)
            libvirt.check_exit_status(ret)

        # Destroy VM first
        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Build vm xml.
        dparams = {}
        if device_num > 1:
            for i in xrange(device_num):
                rng_model = params.get("rng_model_%s" % i, "virtio")
                dparams[i] = {"rng_model": rng_model}
                dparams[i].update({"backend_model": params.get(
                    "backend_model_%s" % i, "random")})
                dparams[i].update({"rng_device": get_rng_device(
                    guest_arch, rng_model)})
                bk_type = params.get("backend_type_%s" % i)
                if bk_type:
                    dparams[i].update({"backend_type": bk_type})
                bk_dev = params.get("backend_dev_%s" % i)
                if bk_dev:
                    dparams[i].update({"backend_dev": bk_dev})
                bk_src = params.get("backend_source_%s" % i)
                if bk_src:
                    dparams[i].update({"backend_source": bk_src})
                bk_pro = params.get("backend_protocol_%s" % i)
                if bk_pro:
                    dparams[i].update({"backend_protocol": bk_pro})
                modify_rng_xml(dparams[i], False)
        else:
            params.update({"rng_device": get_rng_device(
                guest_arch, params.get("rng_model", "virtio"))})

            if detach_alias:
                device_alias = "ua-" + str(uuid.uuid4())
                params.update({"rng_alias": device_alias})

            rng_xml = modify_rng_xml(params, not test_snapshot, attach_rng)

        try:
            # Add random server
            if random_source and params.get("backend_type") == "tcp" and not test_guest_dump:
                cmd = "cat /dev/random | nc -4 -l localhost 1024"
                bgjob = utils_misc.AsyncJob(cmd)

            vm.start()
            if attach_rng:
                ret = virsh.attach_device(vm_name, rng_xml.xml,
                                          flagstr=attach_options,
                                          wait_remove_event=True,
                                          debug=True, ignore_status=True)
                libvirt.check_exit_status(ret, status_error)
                if status_error:
                    return
                if not check_rng_xml(rng_xml, True):
                    test.fail("Can not find rng device in xml")

            else:
                # Start the VM.
                if start_error:
                    test.fail("VM started unexpectedly")

            if test_qemu_cmd and not attach_rng:
                if device_num > 1:
                    for i in xrange(device_num):
                        check_qemu_cmd(dparams[i])
                else:
                    check_qemu_cmd(params)
            if test_host:
                check_host()
            session = vm.wait_for_login()
            if test_guest:
                check_guest(session)
            if test_guest_dump:
                if params.get("backend_type") == "tcp":
                    cmd = "cat /dev/random | nc -4 localhost 1024"
                    bgjob = utils_misc.AsyncJob(cmd)
                check_guest_dump(session, True)
            if test_snapshot:
                check_snapshot(bgjob)

            if detach_alias:
                result = virsh.detach_device_alias(vm_name, device_alias,
                                                   detach_alias_options, debug=True)
                if "--config" in detach_alias_options:
                    vm.destroy()

                def have_rng_xml():
                    """
                    check if xml have rng item
                    """
                    output = virsh.dumpxml(vm_name)
                    return not output.stdout.strip().count("<rng model=")

                if utils_misc.wait_for(have_rng_xml, wait_timeout):
                    logging.info("Cannot find rng device in xml after detach")
                else:
                    test.fail("Found rng device in xml after detach")

            # Detach after attach
            if attach_rng:
                ret = virsh.detach_device(vm_name, rng_xml.xml,
                                          flagstr=attach_options,
                                          debug=True, ignore_status=True)
                libvirt.check_exit_status(ret, status_error)
                if utils_misc.wait_for(lambda: check_rng_xml(rng_xml, False), wait_timeout):
                    logging.info("Find same rng xml as hotpluged")
                else:
                    test.fail("Rng device still exists after detach!")

                if test_guest_dump:
                    check_guest_dump(session, False)

            session.close()
        except virt_vm.VMStartError as details:
            logging.info(str(details))
            if not start_error:
                test.fail('VM failed to start, '
                          'please refer to https://bugzilla.'
                          'redhat.com/show_bug.cgi?id=1220252:'
                          '\n%s' % details)
    finally:
        # Delete snapshots.
        snapshot_lists = virsh.snapshot_list(vm_name, debug=True)
        if len(snapshot_lists) > 0:
            libvirt.clean_up_snapshots(vm_name, snapshot_lists)
            for snapshot in snapshot_lists:
                virsh.snapshot_delete(vm_name, snapshot, "--metadata", debug=True)

        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        vmxml_backup.sync()
        if bgjob:
            bgjob.kill_func()
