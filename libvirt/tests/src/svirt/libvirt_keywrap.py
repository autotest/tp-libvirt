from virttest.libvirt_xml.vm_xml import VMXML, VMKeywrapXML
from virttest.utils_libvirt.libvirt_keywrap import ProtectedKeyHelper


def run(test, params, env):
    """
    Test keywrap handling for s390x guests

    :param test: test instance
    :param params: test parameters
    :param env: test environment
    :return: None
    """
    default = params.get("default", "yes") == "yes"
    vm_name = params.get("main_vm")
    expect_token = params.get("expect_token", "yes") == "yes"
    name = params.get("keyname", None)
    state = params.get("keystate", None)

    vm = env.get_vm(vm_name)

    vmxml_backup = VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        if default:
            vmxml.del_keywrap()
        else:
            kw = VMKeywrapXML()
            kw.set_cipher(name, state)
            vmxml.set_keywrap(kw)

        vmxml.sync()
        vm.start()
        session = vm.wait_for_login()

        pkey_helper = ProtectedKeyHelper(session)
        pkey_helper.load_module()

        token = pkey_helper.get_some_aes_key_token()
        if expect_token and token is None:
            test.fail("Didn't receive expected key token."
                      " Please check debug log.")
        elif not expect_token and token is not None:
            test.fail("Received key token though none expected."
                      " Please check debug log.")
    finally:
        vmxml_backup.sync()
