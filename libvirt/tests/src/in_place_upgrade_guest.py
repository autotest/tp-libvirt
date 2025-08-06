# pylint: disable=spelling
# disable pylint spell checker to allow for leapp and preupgrade
import re
import os
import textwrap

from avocado.utils import process

from virttest import utils_package
from virttest import utils_test


def run(test, params, env):
    """
    This case verifies that an in place upgrade on a guest succeeds.
    This test only supports upgrading a RHEL X-1 guest to RHEL X on a RHEL X host.
    This test ensures that, when relevant, the page size does not change after upgrade.

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

        source_release, target_release = get_release_info(test, params, session)
        check_support(test, source_release, target_release)

        pagesize_check_cmd = params.get("pagesize_check_cmd")
        target_page_size = session.cmd_output(pagesize_check_cmd).strip()

        utils_package.package_install(["leapp-upgrade*"], session=session, timeout=360)

        prepare_repos_in_guest(test, params, vm, target_release)

        run_leapp_cmd(test, params, session, step="preupgrade")
        run_leapp_cmd(test, params, session, step="upgrade")
        session = vm.reboot(session=session, timeout=900)

        verify_upgrade_succeeded(test, params, vm, session, target_release, target_page_size)

        session.close()

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)


def get_release_info(test, params, session):
    """
    Get the source release and target release versions

    :params test: test object
    :params params: wrapped dict with all parameters
    :params session: the vm session
    :return: tuple, (source  version, target  version)
    """
    release_check_cmd = params.get("release_check_cmd")
    test.log.debug("Getting source release (same as release on vm)")
    source_release = extract_release_version(test, session.cmd_output(release_check_cmd))
    test.log.debug("Getting target release (same as release on host)")
    target_release = extract_release_version(test, process.run(release_check_cmd, shell=True).stdout_text)
    return (source_release, target_release)


def extract_release_version(test, release_output):
    """
    Extract the release version from the full release output

    :params test: test object
    :params release_output: output from running release_check_cmd
    """
    test.log.debug("Full release output: %s" % release_output)
    return re.findall("\d+\.\d+", release_output)[0]


def check_support(test, source_release, target_release):
    """
    Check whether the test supports upgrading from source release to target release

    :params test: test object
    :params source_release: source release version (upgrading from version)
    :params target_release: target release version (upgrading to version)
    """
    source_major_release = int(source_release.split(".")[0])
    target_major_release = int(target_release.split(".")[0])

    if (source_major_release + 1 != target_major_release):
        test.cancel("Can not upgrade guest from rhel %s to rhel %s - "
                    "This test only supports upgrading a RHEL X-1 guest to RHEL X on a RHEL X host" %
                    (source_release, target_release))
    else:
        test.log.info("Upgrading guest from rhel %s to rhel %s on a rhel %s host" %
                      (source_release, target_release, target_release))


def prepare_repos_in_guest(test, params, vm, target_release):
    """
    Prepare repos in the guest by including the upgrade repos paths

    :params test: test object
    :params params: wrapped dict with all parameters
    :params session: the vm
    :params target_release: target release version
    """
    compose_url = params.get("compose_url")
    upgrade_repos_path = params.get("upgrade_repos_path")
    vm_arch_name = params.get("vm_arch_name")

    target_major_release = target_release.split(".")[0]

    upgrade_repos_content = textwrap.dedent(f"""
        [APPSTREAM]
        name=APPSTREAM
        baseurl={compose_url}/rhel-{target_major_release}/nightly/RHEL-{target_major_release}/latest-RHEL-{target_release}/compose/AppStream/{vm_arch_name}/os/
        enabled=1
        gpgcheck=0
        [BASEOS]
        name=BASEOS
        baseurl={compose_url}/rhel-{target_major_release}/nightly/RHEL-{target_major_release}/latest-RHEL-{target_release}/compose/BaseOS/{vm_arch_name}/os/
        enabled=1
        gpgcheck=0
    """)

    test.log.debug("Temporarily creating leapp_upgrade_repositories file on the host: %s" % upgrade_repos_content)
    tmp_upgrade_repos_path = os.path.join(test.debugdir, "tmp_leapp_upgrade_repositories.repo")
    with open(tmp_upgrade_repos_path, 'w+') as f:
        f.write(upgrade_repos_content)

    test.log.info("Copying leapp_upgrade_repositories file onto the guest")
    vm.copy_files_to(host_path=tmp_upgrade_repos_path, guest_path=upgrade_repos_path)


def run_leapp_cmd(test, params, session, step):
    """
    Run the leapp (pre)upgrade cmd

    :params test: test object
    :params params: wrapped dict with all parameters
    :params session: the vm session
    :params step: "preupgrade" or "upgrade"
    """
    leapp_cmd = params.get("leapp_preupgrade_cmd")
    if step == "upgrade":
        leapp_cmd = params.get("leapp_upgrade_cmd")

    # May need to test versions that are not yet officially supported by leapp
    # despite it being technically capable of the upgrade
    leapp_skip_check_os = params.get("leapp_skip_check_os")
    leapp_cmd = "%s %s" % (leapp_skip_check_os, leapp_cmd)

    status = session.cmd_status(leapp_cmd, timeout=900)
    if status == 0:
        test.log.info("Leapp %s executed successfully" % step)
    else:
        test.fail("Leapp %s failed, see logs for more info" % step)


def verify_upgrade_succeeded(test, params, vm, session, target_release, target_page_size):
    """
    Verify whether the upgrade succeeded by checking
    the release version, the kernel version, and the page size

    :params test: test object
    :params params: wrapped dict with all parameters
    :params vm: the vm
    :params session: the vm session
    :params target_release: target release version (guest version after upgrade)
    :params target_page_size: target page size (result of pagesize_check_cmd)
    """
    vm_arch_name = params.get("vm_arch_name")
    release_check_cmd = params.get("release_check_cmd")
    kernel_check_cmd = params.get("kernel_check_cmd")
    pagesize_check_cmd = params.get("pagesize_check_cmd")

    test.log.debug("Getting guest release after upgrade")
    post_release_output = extract_release_version(test, session.cmd_output(release_check_cmd))
    if target_release not in post_release_output:
        test.fail("The guest after upgrade should be at rhel %s, but is at %s" %
                  (target_release, post_release_output))
    else:
        test.log.info("The guest after upgrade is at rhel %s, as expected" % post_release_output)

    target_major_release = target_release.split(".")[0]
    test.log.debug("Getting guest kernel version after upgrade")
    post_kernel_output = session.cmd_output(kernel_check_cmd).strip()
    if (".el%s" % target_major_release) not in post_kernel_output:
        test.fail("The guest after upgrade should be at kernel version .el%s, but is at %s" %
                  (target_major_release, post_kernel_output))
    else:
        test.log.info("The guest after upgrade is at kernel version .el%s (%s), as expected" %
                      (target_major_release, post_kernel_output))

    post_page_size = session.cmd_output(pagesize_check_cmd).strip()
    if vm_arch_name == "aarch64" and target_page_size != post_page_size:
        # Note: the following is a workaround that should be removed after the leapp tool provides 64k support on aarch64
        test.log.info("Guest page size before upgrade (%s) does not match page size after upgrade (%s)" %
                      (target_page_size, post_page_size))
        test.log.info("Attempting temporary workaround to update the guest default kernel")
        kernel_pattern = ".*el%s*.*%sk" % (target_major_release, int(target_page_size) // 1024)
        kernel_version = utils_test.get_available_kernel_paths(session, kernel_pattern)[0]
        utils_test.update_vm_default_kernel(vm, kernel_version, reboot=True, guest_arch_name=vm_arch_name, timeout=900)

        session = vm.wait_for_login()
        post_page_size = session.cmd_output(pagesize_check_cmd).strip()
        if target_page_size != post_page_size:
            test.fail("Guest page size before upgrade (%s) does not match page size after upgrade and workaround (%s)" %
                      (target_page_size, post_page_size))
        else:
            test.log.info("Guest page size after upgrade and workaround is %s, as expected" % post_page_size)
