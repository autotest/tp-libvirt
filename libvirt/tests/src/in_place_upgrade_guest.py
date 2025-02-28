# pylint: disable=spelling
# disable pylint spell checker to allow for leapp and preupgrade
import textwrap

from avocado.utils import process

from virttest import utils_package


def run_leapp_cmd(test, params, session, step):
    leapp_cmd = params.get("leapp_preupgrade_cmd")
    if step == "upgrade":
        leapp_cmd = params.get("leapp_upgrade_cmd")

    leapp_status, leapp_output = session.cmd_status_output(leapp_cmd, timeout=900)
    if leapp_status == 0:
        test.log.info("Leapp %s executed successfully" % step)
    else:
        test.log.debug("Leapp %s output: %s" % (step, leapp_output))
        test.fail("Leapp %s failed, see log for more info" % step)


def run(test, params, env):
    """
    This case verifies that an in place upgrade on a guest succeeds
    1) Prepare a running guest
    2) Calculate target release and pagesize
    3) Install leapp tool
    4) Configure leapp tool
    5) Run leapp preupgrade and upgrade cmds
    6) Reboot vm to complete upgrade
    7) Verify that the upgrade succeeded and versions are as expected
    8) If the kernel pagesize is incorrect,
        attempt to find the correct kernel, set it as the default, reboot guest, and check again
    """
    vm_name = params.get("main_vm")
    vm_arch_name = params.get("vm_arch_name")
    release_check_cmd = params.get("release_check_cmd")
    kernel_check_cmd = params.get("kernel_check_cmd")
    pagesize_check_cmd = params.get("pagesize_check_cmd")
    compose_url = params.get("compose_url")
    upgrade_repos_path = params.get("upgrade_repos_path")
    target_minor_release = params.get("target_minor_release")

    # 1) Prepare a running guest
    vm = env.get_vm(vm_name)

    try:
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()

        # 2) Calculate target release and pagesize
        # Support for upgrading a RHEL X-1 guest to RHEL X on a RHEL X host
        pre_release = session.cmd_output(release_check_cmd).strip()
        pre_major_release = pre_release.partition(".")[0]
        target_release = process.run(release_check_cmd, shell=True).stdout_text.strip()
        target_major_release = target_release.partition(".")[0]

        if (int(pre_major_release) + 1 != int(target_major_release)):
            test.cancel("Can not upgrade guest from rhel %s to rhel %s - "
                        "This test only supports upgrading a RHEL X-1 guest to RHEL X on a RHEL X host"
                        % (pre_release, target_release))
        else:
            test.log.info("Upgrading guest from rhel %s to rhel %s on a rhel %s host" % (pre_release, target_release, target_release))

        # The page size should stay the same after upgrade
        target_page_size = session.cmd_output(pagesize_check_cmd).strip()

        # 3) Install leapp tool
        utils_package.package_install(["leapp-upgrade*"], session=session, timeout=360)

        # 4) Configure leapp tool
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

        # 5) Run leapp preupgrade and upgrade cmds
        run_leapp_cmd(test, params, session, step="preupgrade")
        run_leapp_cmd(test, params, session, step="upgrade")

        # 6) Reboot vm to complete upgrade
        session = vm.reboot(session=session, timeout=900)

        # 7) Verify that the upgrade succeeded and versions are as expected
        release_output = session.cmd_output(release_check_cmd).strip()
        if target_release != release_output:
            test.fail("The guest after upgrade should be at rhel %s, but is at rhel %s" %
                      (target_release, release_output))

        kernel_output = session.cmd_output(kernel_check_cmd)
        if (".el%s" % target_major_release) not in kernel_output:
            test.fail("The guest after upgrade should be at kernel version .el%s, but is at %s" %
                      (target_major_release, kernel_output))

        post_page_size = session.cmd_output(pagesize_check_cmd).strip()
        if target_page_size != post_page_size:
            # 8) If the kernel pagesize is incorrect, ...
            list_kernel_cmd = params.get("list_kernel_cmd")
            default_kernel_cmd = params.get("default_kernel_cmd")

            # attempt to find the correct kernel...
            kernels = session.cmd_output(list_kernel_cmd).splitlines()
            release = ".el%s" % target_major_release
            pagesize = "+%sk" % (int(target_page_size) // 1024)
            try:
                kernel_index = [i for i, kernel in enumerate(kernels) if release in kernel and pagesize in kernel][0]
            except:
                test.fail("After upgrade, a kernel with version %s and pagesize %s could not be found" % (release, pagesize))

            # set it as the default...
            session.cmd("%s %s" % (default_kernel_cmd, kernel_index))

            # reboot guest...
            session = vm.reboot(session=session, timeout=900)

            # and check again
            post_page_size = session.cmd_output(pagesize_check_cmd).strip()
            if target_page_size != post_page_size:
                test.fail("Page size before upgrade (%s) is not the same after upgrade (%s)" % (target_page_size, post_page_size))

        session.close()

    finally:
        # Destroy VM
        if vm.is_alive():
            vm.destroy(gracefully=False)
