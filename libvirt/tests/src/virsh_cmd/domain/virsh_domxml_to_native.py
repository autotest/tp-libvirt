import re
import os
import logging

from avocado.utils import process

from virttest import virsh
from virttest import utils_libvirtd
from virttest.compat_52lts import decode_to_text as to_text


def run(test, params, env):
    """
    Test command: virsh domxml-to-native.

    Convert domain XML config to a native guest configuration format.
    1.Prepare test environment.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Perform virsh domxml-from-native operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()

    def buildcmd(arglist):
        """
        Return a list of arguments of qemu command.

        Return a list based on the input string where each list element
        is put together with care to pair up options with their argument
        rather than being on separate lines.  Thus rather than having
        "-option" "argument" in separate list elements, they will be in
        one element "-option argument". Take care to note the argument to
        an option may not be required. This will make it easier to determine
        what is causing the failure when printing error messages.
        """
        # First separate everything by the first space into a list
        elems = arglist.split('\x20')

        # Peruse the list to build up a formatted output retlist
        retlist = []
        i = 0
        skip = False
        for e in elems:
            # If 'skip' is True, then we've appended an option and argument
            if skip:
                skip = False
                i = i + 1
                continue

            # Need a peek at the next element
            enext = elems[i + 1]

            # If current and next element starts with "-", then the
            # is not an argument to the current, thus we just append.
            # Same for anything we find that doesn't start with a "-"
            if (e[0] == '-' and enext[0] == '-') or e[0] != '-':
                retlist.append(e)
            else:
                # Append this and the next and set our skip flag
                retlist.append(e + " " + enext)
                skip = True
            i = i + 1

        # Now build a list where the
        return retlist

    def filtlist(arglist):
        """
        Return a filtered list of arguments.

        Walk through the supplied list to filter out things that will be
        known to be different depending on the running environment.
        """
        retlist = []
        for arg in arglist:
            if re.search("mode=readline", arg):
                continue
            elif re.search("mac=", arg):
                continue
            elif re.search("127.0.0.1:", arg):
                continue
            elif re.search("tap", arg):
                continue
            # Upstream libvirt commit id 'e8400564':
            # XMLToNative: Don't show -S
            elif re.search("-S", arg):
                continue
            elif re.search("socket,id=", arg):
                continue
            elif re.search("secret,id=", arg):
                continue
            elif re.search("-cpu", arg):
                continue
            retlist.append(arg)

        return retlist

    def compare(conv_arg):
        """
        Compare converted information with vm's information.

        :param conv_arg : Converted information.
        :return: True if converted information has no different from
                 vm's information.
        """
        pid = vm.get_pid()
        cmdline_tmp = to_text(process.system_output("cat -v /proc/%d/cmdline" % pid, shell=True))

        # Output has a trailing '^@' which gets converted into an empty
        # element when spliting by '\x20', so strip it on the end.
        cmdline = re.sub(r'\^@', ' ', cmdline_tmp).strip(' ')

        # Fedora 19 replaces the /usr/bin/qemu-kvm with the string
        # "/usr/bin/qemu-system-x86_64 -machine accel=kvm", so let's
        # do the same if we find "/usr/bin/qemu-kvm" in the incoming
        # argument list and we find "qemu-system-x86_64 -machine accel=kvm"
        # in the running guest's cmdline
        # ubuntu use /usr/bin/kvm as qemu binary
        qemu_bin = ["/usr/bin/qemu-kvm", "/usr/bin/kvm"]
        arch_bin = ["/usr/bin/qemu-system-x86_64 -machine accel=kvm",
                    "/usr/bin/qemu-system-ppc64 -machine accel=kvm",
                    "qemu-system-ppc64 -enable-kvm"]
        qemu_kvm_bin = ""
        for each_bin in qemu_bin:
            if conv_arg.find(each_bin) != -1:
                qemu_kvm_bin = each_bin
        if qemu_kvm_bin:
            for arch in arch_bin:
                if cmdline.find(arch) != -1:
                    cmdline = re.sub(arch, qemu_kvm_bin, cmdline)
        else:
            logging.warning("qemu-kvm binary is not identified: '%s'",
                            qemu_kvm_bin)

        # Now prepend the various environment variables that will be in
        # the conv_arg, but not in the actual command
        tmp = re.search('LC_ALL.[^\s]\s', conv_arg).group(0) +\
            re.search('PATH.[^\s]+\s', conv_arg).group(0) +\
            re.search('QEMU_AUDIO_DRV.[^\s]+\s', conv_arg).group(0)
        qemu_arg = tmp + cmdline

        conv_arg_lines = buildcmd(conv_arg)
        qemu_arg_lines = buildcmd(qemu_arg)

        diff1 = filtlist(tuple(x for x in conv_arg_lines
                               if x not in set(qemu_arg_lines)))
        if diff1:
            logging.debug("Found the following in conv_arg not in qemu_arg:")
        for elem in diff1:
            logging.debug("\t%s", elem)

        diff2 = filtlist(tuple(x for x in qemu_arg_lines
                               if x not in set(conv_arg_lines)))
        if diff2:
            logging.debug("Found the following in qemu_arg not in conv_arg:")
        for elem in diff2:
            logging.debug("\t%s", elem)

        if diff1 or diff2:
            return False

        return True

    # prepare
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    if not vm.is_dead():
        vm.destroy()
    vm.start()
    if not vm.is_alive():
        test.fail("VM start failed")

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    dtn_format = params.get("dtn_format")
    file_xml = params.get("dtn_file_xml", "")
    extra_param = params.get("dtn_extra_param")
    extra = params.get("dtn_extra", "")
    libvirtd = params.get("libvirtd")
    status_error = params.get("status_error", "no")
    vm_id = params.get("dtn_vm_id", "")
    readonly = ("yes" == params.get("readonly", "no"))

    # For positive_test
    if status_error == "no":
        if vm_id == "id":
            vm_id = domid
        elif vm_id == "uuid":
            vm_id = domuuid
        elif vm_id == "name":
            vm_id = "%s %s" % (vm_name, extra)
        if file_xml == "":
            extra_param = extra_param + vm_id

    virsh.dumpxml(vm_name, extra="", to_file=file_xml)
    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    # run test case
    ret = virsh.domxml_to_native(dtn_format, file_xml, extra_param, readonly=readonly,
                                 ignore_status=True, debug=True)
    status = ret.exit_status
    conv_arg = ret.stdout.strip()

    # recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # clean up
    if os.path.exists(file_xml):
        os.remove(file_xml)

    # check status_error
    if status_error == "yes":
        if status == 0:
            test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if status != 0:
            test.fail("Run failed with right command")
        if compare(conv_arg) is not True:
            test.fail("Test failed!")
