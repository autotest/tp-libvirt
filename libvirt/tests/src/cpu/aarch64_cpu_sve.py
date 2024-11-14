import json
import re
import logging as log

from avocado.core import exceptions
from avocado.utils import process

from virttest import virsh
from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import LibvirtXMLError
from virttest.utils_test import libvirt


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def get_vector_lengths(vm_name):
    """
    Get the supported and unsupported vector lengths on the host

    :param vm_name: str, the vm name
    :return: (dict, dict), the supported and unsupported vector list
    """
    cmd = '{"execute":"query-cpu-model-expansion", "arguments": {"type": "full", "model": {"name":"host"}}}'
    virsh_opt = {"debug": True, "ignore_status": False}
    unsupported_list = []
    supported_list = []
    ret = virsh.qemu_monitor_command(vm_name, cmd, **virsh_opt)
    length_obj = json.loads(ret.stdout_text.strip())
    props_dict = length_obj.get("return").get("model").get("props")
    for v_length, status in props_dict.items():
        match = re.findall("sve(\d+)", v_length)
        if not match:
            continue
        if not status:
            unsupported_list.append(int(match[0]))
        elif status:
            supported_list.append(int(match[0]))
    supported_list.sort(reverse=True)
    unsupported_list.sort(reverse=True)
    logging.debug("The unsupported vector length in the vm:%s", unsupported_list)
    logging.debug("The supported vector length in the vm:%s", supported_list)
    return (supported_list, unsupported_list)


def prepare_env(vm, params, test):
    """
    Prepare test env

    :param vm: The virtual machine
    :param params: dict, test parameters
    :param test: test object
    """
    check_sve = params.get("check_sve", "")
    host_without_sve = "yes" == params.get("host_without_sve", "no")
    check_sve_config = params.get("check_sve_config", "")
    session = None
    try:
        # Cancel test if the Host doesn't support or supports SVE based on configuration
        if process.run(check_sve, ignore_status=True, shell=True).exit_status:
            if not host_without_sve:
                test.cancel("Host doesn't support SVE")
            else:
                return
        else:
            if host_without_sve:
                test.cancel("Host supports SVE")

        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login(timeout=120)
        current_boot = session.cmd("uname -r").strip()
        # To enable SVE: Hardware support && enable kconfig
        # CONFIG_ARM64_SVE
        if session.cmd_status(check_sve_config % current_boot):
            test.cancel("Guest kernel doesn't enable CONFIG_ARM64_SVE")

        # Install lscpu tool that check whether CPU has SVE
        if (not utils_package.package_install("util-linux")
                or not utils_package.package_install("util-linux", session)):
            test.error("Failed to install util-linux")

    except (exceptions.TestCancel, exceptions.TestError):
        raise
    except Exception as e:
        test.error("Failed to prepare test env: %s" % e)
    finally:
        if session:
            session.close()


def get_max_sve_len_in_guest(vm_session, params, test):
    """
    Get the maximum supported sve length of guest

    :param vm_session: The virtual machine session
    :param params: dict, test parameters
    :param test: test object
    :return: str, maximum vector length. Format: e.g sve512
    """
    get_maximum_sve_len = params.get("get_maximum_sve_length", "")
    sve_length_bit = ""
    try:
        ret = vm_session.cmd(get_maximum_sve_len).strip()
        # dmesg record maximum sve length in bytes
        sve_length_byte = re.search(r"length (\d+) bytes", ret).groups()[0]
        # Change max_length into sve + length(bit) E.g. sve512
        sve_length_bit = "sve" + str(int(sve_length_byte) * 8)
        logging.debug("The guest maximum SVE vector length is %s", sve_length_bit)
    except Exception as e:
        test.fail("Failed to get maximum guest SVE vector length: %s" % e)

    return sve_length_bit


def guest_has_sve(vm_session, params, test):
    """
    Check whether guest has SVE

    :param vm_session: The virtual machine session
    :param params: dict, test parameters
    :param test: test object

    :return True if guest has sve
    """
    check_sve = params.get("check_sve", "")
    ret = False
    try:
        if not vm_session.cmd_status(check_sve):
            ret = True
    except Exception as e:
        test.error("Failed to check guest SVE: %s" % e)
    return ret


def check_vector_length_supported(vector_length, supported_list):
    """
    Check if a given vector length is supported on the host

    :param vector_length: str, sve length
    :param supported_list: list, sve lengths supported
    :return: bool, True if supported, otherwise False
    """
    match = re.findall("sve(\d+)", vector_length)
    if match and int(match[0]) in supported_list:
        return True
    else:
        logging.warning("The vector length %s (in bits) is "
                        "unsupported on the host" % vector_length)
        return False


def gen_disable_features(enable_length, supported_list):
    """
    Generate the cpu features to be disabled

    :param enable_length: str, sve length to be enabled
    :param supported_list: dict, sve lengths supported
    :return: list, disabled cpu features
    """
    result = []
    enable_length = re.findall("sve(\d+)", enable_length)[0]
    for a_length in supported_list:
        if a_length > int(enable_length):
            result.append({"sve%d" % a_length: "disable"})
    return result


def gen_cpu_features(supported_list, unsupported_list, params, test):
    """
    Generate cpu features

    :param supported_list: dict, sve lengths supported
    :param unsupported_list: dict, sve lengths unsupported
    :param params: dict, test parameters
    :param test: test object
    :return: list, cpu features
    """
    all_supports = params.get("all_supports", "no") == "yes"
    status_error = "yes" == params.get("status_error", "no")
    vector_length = params.get("vector_length")
    cpu_xml_policy = params.get("cpu_xml_policy", "require")
    unsupported_len = params.get("unsupported_len")
    discontinous_len = params.get("discontinous_len")
    result = []

    if not status_error:
        if all_supports:
            result.append({"sve": cpu_xml_policy})
            for a_length in supported_list:
                result.append({"sve%d" % a_length: cpu_xml_policy})
            return result
        if vector_length:
            if not check_vector_length_supported(vector_length, supported_list):
                test.cancel("The vector length is not supported on the host")
            result.append({vector_length: cpu_xml_policy})
            result.extend(gen_disable_features(vector_length, supported_list))
        else:
            result.append({"sve": cpu_xml_policy})
    else:
        vector_length_list = params.get("vector_length_list")
        if unsupported_len:
            if len(unsupported_list) > 1:
                result.append({"sve%d" % unsupported_list[0]: cpu_xml_policy})
            else:
                test.cancel("No unsupported vector length by the host is available")
        elif discontinous_len:
            if len(supported_list) == 1:
                test.cancel("Require at least two supported "
                            "sve lengths for the test, but only one is found")
            result.append({"sve%d" % supported_list[0]: "require"})
            result.append({"sve%d" % supported_list[len(supported_list) - 1]: "disable"})
        elif vector_length_list:
            vector_length_list = eval(vector_length_list)
            for one_len in vector_length_list:
                result.append(one_len)
        else:
            if vector_length:
                result.append({vector_length: cpu_xml_policy})
    return result


def update_cpu_xml(cpu_xml, params, test, supported_list, unsupported_list):
    """
    Update vm cpu xml object

    :param cpu_xml: VMCPUXML instance
    :param params: dict, test parameters
    :param test: test object
    :param supported_list: list, sve lengths supported
    :param unsupported_list: list, sve lengths unsupported
    """
    if not supported_list and not unsupported_list:
        # There is no sve support on the host.
        # This is to expect an error message with sve=require
        # when starting the vm.
        vector_list = [{'sve': params.get("cpu_xml_policy")}]
    else:
        vector_list = gen_cpu_features(supported_list,
                                       unsupported_list,
                                       params,
                                       test)
    for vector in vector_list:
        for length, policy in vector.items():
            cpu_xml.add_feature(length, policy)
    logging.debug("cpu_xml is %s" % cpu_xml)


def execute_cmds(cmd, session, test, timeout=360, ignore_status=False):
    """
    Execute a command with vm session

    :param cmd: str, the command to be executed
    :param session: vm session
    :param test: test object
    :param timeout: int, defaults to 360
    :param ignore_status: bool, defaults to False
    :return: str, the output of the command
    """
    status, output = session.cmd_status_output(cmd, timeout=timeout)
    if status:
        msg = "Fail to execute %s with error %s,\
               message: %s" % (cmd, status, output)
        if not ignore_status:
            test.fail(msg)
        else:
            test.log.error(msg)
    return output


def run_optimized_routines_in_guest(session, params, test):
    """
    Run optimized routines test suite within the guest

    :param session: vm session
    :param params: dict, test parameters
    :param test: test object
    """
    execute_cmds(params.get("optimized_repo_cmd"), session, test)
    execute_cmds(params.get("optimized_compile_cmd"), session, test)
    output = execute_cmds(params.get("optimized_execute_cmd"), session, test)
    results = re.findall(r"^(\w+) \w+sve$", output, re.M)
    if not all([result == "PASS" for result in results]):
        test.fail("The optimized-routines sve tests have failures in log\n%s" % results)
    else:
        test.log.debug("Verify optimized-routines sve tests in guest - PASS")


def execute_sve_stress_by_suite(length_list, session, params, test):
    """
    Execute sve_stress test suite within the guest

    :param length_list: list, sve lengths
    :param session: vm session
    :param params: dict, test parameters
    :param test: test object
    """
    sve_stress_exec_cmd = params.get("sve_stress_exec_cmd")
    sve_exec_timeout = int(params.get("sve_exec_timeout"))

    for a_length in length_list:
        exec_cmd = sve_stress_exec_cmd % a_length
        output = session.cmd_output(exec_cmd,
                                    timeout=sve_exec_timeout + 10)
        results_lines = [result for result in output.splitlines() if
                         result.startswith("Terminated by")]
        if len(re.findall(r"no error", output, re.M)) != len(results_lines):
            test.log.debug("Test results: %s", results_lines)
            test.fail("SVE stress test failed")
        test.log.debug("Verify sve stress test for length %s (in bytes) - PASS" % a_length)


def run_sve_ptrace_test_in_guest(session, params, test):
    """
    Run sve_ptrace test suite within the guest

    :param session: vm session
    :param params: dict, test parameters
    :param test: test object
    """
    sve_ptrace_exec_cmd = params.get("sve_ptrace_exec_cmd")
    output = session.cmd_output(sve_ptrace_exec_cmd)
    results_lines = [result for result in output.splitlines() if
                     result.startswith("# Totals:")]
    pat = "fail:(\d+).*xfail:(\d+).*error:(\d+)"
    match = re.findall(pat, results_lines[0])
    if match:
        if any([int(match[0][0]), int(match[0][1]), int(match[0][2])]):
            test.fail("The sve-ptrace test fail with errors:%s" % results_lines[0])
    else:
        test.error("Can not get Totals: line from test result")
    test.log.debug("Verify sve ptrace test - PASS")


def verify_sve_length_by_suite(supported_list, session, params, test):
    """
    Verify the supported sve lengths are same with test suite output

    :param supported_list: list, supported sve length (in bits), like [128, 256]
    :param session: vm session
    :param params: dict, test parameters
    :param test: test object
    :return: list, sve length (in bytes) list from test suite command
                   like ['16', '32']
    """
    sve_stress_get_lenths = params.get("sve_stress_get_lenths")
    output = execute_cmds(sve_stress_get_lenths, session, test)
    results = re.findall(r"# (\d+)$", output, re.M)
    new_results = [int(result) * 8 for result in results]
    if set(new_results) != set(supported_list):
        test.fail("The sve-probe-vls output %s does "
                  "not match the supported sve lenths %s" % (results,
                                                             supported_list))
    else:
        test.log.debug("Verify sve-probe-vls output in guest - PASS")
    return results


def prepare_kernel_selftest_in_guest(session, params, test):
    """
    Prepare the kernel self test repo in the guest

    :param session: vm session
    :param params: dict, test parameters
    :param test: test object
    """
    kernel_version = session.cmd_output("uname -r").rsplit('.', 1)[0]
    srpm = "kernel-%s.src.rpm" % kernel_version
    linux_name = "linux-%s.tar.xz" % kernel_version
    kernel_download_cmd = params.get("kernel_download_cmd") % (srpm,
                                                               srpm)
    kernel_tar_cmd = params.get("kernel_tar_cmd") % linux_name
    kernel_selftest_compile_cmd = params.get("kernel_selftest_compile_cmd")
    prepare_cert_cmd = params.get("prepare_cert_cmd")
    execute_cmds(prepare_cert_cmd, session, test)
    execute_cmds(kernel_download_cmd, session, test)
    execute_cmds(kernel_tar_cmd, session, test)
    execute_cmds(kernel_selftest_compile_cmd, session, test)


def run_sve_stress_test_in_guest(supported_list, session, params, test):
    """
    Run sve stress test suite within the guest

    :param supported_list: list, supported sve lengths
    :param session: vm session
    :param params: dict, test parameters
    :param test: test object
    """
    sve_lengths = verify_sve_length_by_suite(supported_list, session, params, test)
    execute_sve_stress_by_suite(sve_lengths, session, params, test)


def run(test, params, env):
    """
    Test aarch64 SVE feature

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    cpu_xml_mode = params.get("cpu_xml_mode", "host-passthrough")
    status_error = "yes" == params.get("status_error", "no")
    define_error = "yes" == params.get("define_error", "no")
    expect_sve = "yes" == params.get("expect_sve", "yes")
    expect_msg = params.get("expect_msg", "")
    vector_length = params.get("vector_length", "sve")
    sve_stress_exec_cmd = params.get("sve_stress_exec_cmd")
    sve_ptrace_exec_cmd = params.get("sve_ptrace_exec_cmd")
    optimized_execute_cmd = params.get("optimized_execute_cmd")
    target_dir = params.get("target_dir")
    host_without_sve = "yes" == params.get("host_without_sve")
    supported_list = None
    unsupported_list = None

    prepare_env(vm, params, test)
    if not host_without_sve:
        supported_list, unsupported_list = get_vector_lengths(vm_name)

    if vm.is_alive():
        vm.destroy(gracefully=False)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    original_vm_xml = vmxml.copy()
    try:
        cpu_xml = vm_xml.VMCPUXML()
        cpu_xml.mode = cpu_xml_mode
        update_cpu_xml(cpu_xml, params, test, supported_list, unsupported_list)
        vmxml.cpu = cpu_xml
        try:
            vmxml.sync()
            logging.debug("vmxml is %s" % vmxml)
        except LibvirtXMLError as e:
            if define_error:
                if not re.search(expect_msg, str(e)):
                    test.fail("Expect definition failure: %s but got %s" %
                              (expect_msg, str(e)))
                else:
                    test.log.debug("Verify the expected vm definition error - PASS")
                    return
            else:
                test.error("Failed to define guest: %s" % str(e))

        result = virsh.start(vm_name, debug=True, ignore_status=True)
        libvirt.check_result(result, expected_fails=expect_msg)
        # Test boot successfully
        if not status_error:
            session = vm.wait_for_login(timeout=120)
            if expect_sve:
                # Expect SVE is enabled in domain xml
                if not guest_has_sve(session, params, test):
                    test.fail("Expect guest cpu enable SVE")
                else:
                    test.log.debug("Verify sve is enabled in guest - PASS")
                # Expect SVE is available with only the selected vector
                expect_vector_length = vector_length
                available_maximum_sve_length = get_max_sve_len_in_guest(session, params, test)
                if vector_length == "sve":
                    expect_vector_length = "sve%d" % supported_list[0]
                if expect_vector_length != available_maximum_sve_length:
                    test.fail("Expect guest support %s" % vector_length)
                else:
                    test.log.debug("Verify the supported maximum "
                                   "vector length is %s in guest "
                                   "- PASS" % expect_vector_length)

                install_pkgs = eval(params.get("install_pkgs", "[]"))
                if install_pkgs and not utils_package.package_install(install_pkgs, session, 360):
                    test.error("Failed to install %s on guest." % install_pkgs)

                if target_dir:
                    execute_cmds("mkdir -p %s" % target_dir, session, test)
                    if params.get("kernel_testing_dir"):
                        prepare_kernel_selftest_in_guest(session, params, test)
                    if sve_stress_exec_cmd:
                        run_sve_stress_test_in_guest(supported_list, session, params, test)
                    if sve_ptrace_exec_cmd:
                        run_sve_ptrace_test_in_guest(session, params, test)
                    if optimized_execute_cmd:
                        run_optimized_routines_in_guest(session, params, test)
                    execute_cmds("rm -rf %s" % target_dir, session, test)
            else:
                # Disable SVE in domain xml
                if guest_has_sve(session, params, test):
                    test.fail("Expect guest cpu disable SVE")
                else:
                    test.log.debug("Verify sve is disabled in guest - PASS")
            if session:
                session.close()
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        original_vm_xml.sync()
