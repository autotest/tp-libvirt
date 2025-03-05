# pylint: disable=spelling
# disable pylint spell checker to allow for leapp and preupgrade
import re
import textwrap

from avocado.utils import process

from virttest import utils_package
from virttest import utils_test


def run(test, params, env):
    """
    This case verifies that an in place upgrade on a guest succeeds
    This test only supports upgrading a RHEL X-1 guest to RHEL X on a RHEL X host

    :params test: test object
    :params params: wrapped dict with all parameters
    :params env: environment object
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    try:
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()

        pre_release, target_release = get_release_info(params, session)
        check_support(test, pre_release, target_release)

        pagesize_check_cmd = params.get("pagesize_check_cmd")
        target_page_size = session.cmd_output(pagesize_check_cmd).strip()

        utils_package.package_install(["leapp-upgrade*"], session=session, timeout=360)

        configure_leapp_tool(params, session, target_release)

        run_leapp_cmd(test, params, session, step="preupgrade")
        run_leapp_cmd(test, params, session, step="upgrade")
        session = vm.reboot(session=session, timeout=900)

        verify_upgrade_succeeded(test, params, vm, session, target_release, target_page_size)

        session.close()

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)


def get_release_info(params, session):
    """
    Get the pre release and target release versions
    """
    release_check_cmd = params.get("release_check_cmd")
    pre_release = extract_release_version(session.cmd_output(release_check_cmd))
    target_release = extract_release_version(process.run(release_check_cmd, shell=True).stdout_text)
    return (pre_release, target_release)


def extract_release_version(release_output):
    """
    Extract the release version from the full release output
    """
    return re.findall("\d+\.\d+", release_output)[0]


def check_support(test, pre_release, target_release):
    """
    Check whether the test supports upgrading from pre_release to target_release
    """
    pre_major_release = int(pre_release.partition(".")[0])
    target_major_release = int(target_release.partition(".")[0])

    if (pre_major_release + 1 != target_major_release):
        test.cancel("Can not upgrade guest from rhel %s to rhel %s - "
                    "This test only supports upgrading a RHEL X-1 guest to RHEL X on a RHEL X host" %
                    (pre_release, target_release))
    else:
        test.log.info("Upgrading guest from rhel %s to rhel %s on a rhel %s host" %
                      (pre_release, target_release, target_release))


def configure_leapp_tool(params, session, target_release):
    """
    Configure the leapp tool by including the upgrade repos paths
    """
    compose_url = params.get("compose_url")
    upgrade_repos_path = params.get("upgrade_repos_path")
    vm_arch_name = params.get("vm_arch_name")

    target_major_release = target_release.partition(".")[0]

    session.cmd("touch %s" % upgrade_repos_path)
    upgrade_repos_content = textwrap.dedent(f"""
        [APPSTREAM]
        name=APPSTREAM
        baseurl={compose_url}/rhel-{target_major_release}/nightly/RHEL-{target_major_release}/latest-RHEL-{target_release}/compose/AppStream/{vm_arch_name}/os/
        enabled=0
        gpgcheck=0
        [BASEOS]
        name=BASEOS
        baseurl={compose_url}/rhel-{target_major_release}/nightly/RHEL-{target_major_release}/latest-RHEL-{target_release}/compose/BaseOS/{vm_arch_name}/os/
        enabled=0
        gpgcheck=0
    """)
    session.cmd("cat <<EOF > %s %s\nEOF" % (upgrade_repos_path, upgrade_repos_content))


def run_leapp_cmd(test, params, session, step):
    """
    Run the leapp (pre)upgrade cmd
    """
    leapp_cmd = params.get("leapp_preupgrade_cmd")
    if step == "upgrade":
        leapp_cmd = params.get("leapp_upgrade_cmd")

    status = session.cmd_status(leapp_cmd, timeout=900)
    if status == 0:
        test.log.info("Leapp %s executed successfully" % step)
    else:
        test.fail("Leapp %s failed, see logs for more info" % step)


def verify_upgrade_succeeded(test, params, vm, session, target_release, target_page_size):
    """
    Verify whether the upgrade succeeded by checking
    the release version, the kernel version, and the page size
    """
    vm_arch_name = params.get("vm_arch_name")
    release_check_cmd = params.get("release_check_cmd")
    kernel_check_cmd = params.get("kernel_check_cmd")
    pagesize_check_cmd = params.get("pagesize_check_cmd")

    post_release_output = extract_release_version(session.cmd_output(release_check_cmd))
    if target_release not in post_release_output:
        test.fail("The guest after upgrade should be at rhel %s, but is at %s" %
                  (target_release, post_release_output))

    target_major_release = target_release.partition(".")[0]
    post_kernel_output = session.cmd_output(kernel_check_cmd)
    if (".el%s" % target_major_release) not in post_kernel_output:
        test.fail("The guest after upgrade should be at kernel version .el%s, but is at %s" %
                  (target_major_release, post_kernel_output))

    post_page_size = session.cmd_output(pagesize_check_cmd).strip()
    if target_page_size != post_page_size:
        kernel_version = get_kernel_version(test, params, session, target_release, target_page_size)
        utils_test.update_default_kernel(vm, kernel_version, timeout=900, guest_arch_name=vm_arch_name)

        session = vm.wait_for_login()
        post_page_size = session.cmd_output(pagesize_check_cmd).strip()
        if target_page_size != post_page_size:
            test.fail("Page size before upgrade (%s) is not the same after upgrade (%s)" %
                      (target_page_size, post_page_size))


def get_kernel_version(test, params, session, target_release, target_page_size):
    """
    Get the kernel version given the release version and page size
    """
    list_kernel_cmd = params.get("list_kernel_cmd")

    kernels = session.cmd_output(list_kernel_cmd).splitlines()
    release = ".el%s" % (target_release.partition(".")[0])
    page_size = "+%sk" % (int(target_page_size) // 1024)
    try:
        kernel = [kernel for kernel in kernels if release in kernel and page_size in kernel][0]
        kernel_version = kernel.split("/")[-1].strip('\"')
        return kernel_version
    except:
        test.fail("A kernel with version %s and pagesize %s could not be found" %
                  (release, page_size))
