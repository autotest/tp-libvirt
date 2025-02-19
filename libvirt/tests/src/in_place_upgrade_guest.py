import textwrap

from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.staging import utils_memory

def check_version(test, params, session, expected_release, step):
    release_check_cmd = params.get("release_check_cmd")
    _, release_output = session.cmd_status_output(release_check_cmd)
    if ("release %s" % expected_release) not in release_output:
        test.fail("The guest %s should be at rhel release %s, but is at %s" %
                (step, expected_release, release_output))
    
    kernel_check_cmd = params.get("kernel_check_cmd")
    _, kernel_output = session.cmd_status_output(kernel_check_cmd)
    if (".el%s" % expected_release) not in kernel_output:
        test.fail("The guest %s should be at kernel version .el%s, but is at %s" %
                (step, expected_release, kernel_output))

def run_leapp_cmd(test, params, session, step):
    leapp_cmd = params.get("leapp_preupgrade_cmd")
    if step == "upgrade":
        leapp_cmd = params.get("leapp_upgrade_cmd")

    leapp_status, leapp_output = session.cmd_status_output(leapp_cmd, timeout=900)
    leapp_status = 0
    if leapp_status == 0:
        test.log.info("Leapp %s executed successfully", step)
    else:
        test.log.debug("Leapp %s output: %s" % (step, leapp_output))
        test.fail("Leapp %s failed, see log for more info", step)

def run(test, params, env):
    """
    ADD TEST DESCRIPTION/STEPS HERE
    """
    vm_name = params.get("main_vm")
    vm_arch_name = params.get("vm_arch_name")
    pre_major_release = params.get("pre_major_release")
    target_major_release = params.get("target_major_release")
    target_release = params.get("target_release")
    compose_url = params.get("compose_url")
    upgrade_repos_path = params.get("upgrade_repos_path")

    vm = env.get_vm(vm_name)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        test.log.debug("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ the test")

        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()

        check_version(test, params, session, pre_major_release, "before upgrade")
        pre_page_size = utils_memory.get_huge_page_size(session)

        utils_package.package_install(["leapp-upgrade*"], session, 360)

        cmd = "touch %s" % upgrade_repos_path
        session.cmd(cmd)
        upgrade_repos = textwrap.dedent(f"""
            [APPSTREAM]
            name=APPSTREAM
            baseurl={compose_url}/rhel-{target_major_release}/nightly/RHEL-{target_major_release}/latest-{target_release}/compose/AppStream/{vm_arch_name}/os/
            enabled=0
            gpgcheck=0
            [BASEOS]
            name=BASEOS
            baseurl={compose_url}/rhel-{target_major_release}/nightly/RHEL-{target_major_release}/latest-{target_release}/compose/BaseOS/{vm_arch_name}/os/
            enabled=0
            gpgcheck=0
        """)
        cmd = "cat <<EOF > %s %s\nEOF" % (upgrade_repos_path, upgrade_repos)
        session.cmd(cmd)

        run_leapp_cmd(test, params, session, "preupgrade")
        run_leapp_cmd(test, params, session, "upgrade")

        session = vm.reboot(session=session, timeout=900)

        check_version(test, params, session, target_major_release, "after upgrade")
        post_page_size = utils_memory.get_huge_page_size(session)
        if pre_page_size != post_page_size:
            test.fail("Page size before upgrade (%s) is not the same after upgrade (%s)" % (pre_page_size, post_page_size)) 

        session.close()

    finally:
        # Recover VM
        if vm.is_alive():
            vm.destroy(gracefully=False)
        test.log.info("Restoring vm %s...", vm.name)
        vmxml_backup.sync()
