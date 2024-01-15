import logging as log
import re

from avocado.core import exceptions
from avocado.utils import distro

from virttest import libvirt_version
from virttest import utils_package
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import domcapability_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.libvirt_xml import xcepts

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def install_pkgs(session, pkgs, test):
    """
    Install packages within the vm

    :param session: vm session
    :param pkgs: str or list, package names to install
    :param test: the test object
    :raises: test.error if installation fails
    """
    if pkgs:
        if not utils_package.package_install(pkgs, session=session):
            test.error("Fail to install package '%s'" % pkgs)


def check_cmd_in_guest(cmd_in_guest, vm_session, params, test):
    """
    Execute a command within the guest and do checking

    :param cmd_in_guest: str, commands to execute in the VM
    :param vm_session: aexpect session for the VM
    :param params: dict, parameters to use
                  hidden_attr's value should be a dict
    :param test: test object
    :raises: test.fail if checkpoints fail
    """
    _, output = utils_misc.cmd_status_output(cmd_in_guest,
                                             shell=True,
                                             ignore_status=False,
                                             verbose=True,
                                             session=vm_session)
    logging.debug("Command '%s' result: %s", cmd_in_guest, output)
    hidden_attr = params.get('hidden_attr')
    if hidden_attr:
        (repl, found) = ('not', True) if hidden_attr['kvm_hidden_state'] == 'on' else ('', False)
        if output.count('KVM') == found:
            test.fail("'KVM' is %s expected when state is "
                      "%s" % (repl, hidden_attr['kvm_hidden_state']))
    logging.debug("Checking in check_cmd_in_guest() is successful.")


def get_hyperv_features_in_domcapabilities():
    """
    Get all supported hyperv features on the host

    :return: list, the feature name list
    """
    domcapa_xml = domcapability_xml.DomCapabilityXML()
    features = domcapa_xml.get_features()
    if not features.hyperv_supported == 'yes':
        return None
    for enum_node in features.get_hyperv_enums():
        if enum_node.name != 'features':
            continue
        return [enum_item.value for enum_item in enum_node.values]


def update_hyperv_features(vmxml, hyperv_attr, test):
    """
    Update specified hyperv features in vmxml
    Sample hyperv_attr: {'relaxed': {'state': 'on'}, 'spinlocks': {'state': 'on', 'retries': '4096'}}

    :param vmxml: VMXML instance
    :param hyperv_attr: dict, hyperv features and attributes
    :param test: test object
    """
    vmxml_features = vmxml.features
    if not vmxml_features:
        vmxml_features = vm_xml.VMFeaturesXML()
    try:
        if vmxml_features.get_hyperv():
            vmxml_features.del_hyperv()
    except xcepts.LibvirtXMLNotFoundError:
        test.log.debug("There is no <hyperv> in old vm xml")
    features_hyperv = vm_xml.VMFeaturesHypervXML()
    features_hyperv.setup_attrs(**hyperv_attr)
    vmxml_features.set_hyperv(features_hyperv)
    vmxml.features = vmxml_features


def add_required_timer(vmxml, timer_attrs):
    """
    Add clock configuration in vmxml

    :param vmxml: VMXML instance
    :param timer_attrs: dict, timer attributes
    """
    vm_clock = vmxml.clock
    clock_timers = vm_clock.timers
    new_timer = vm_xml.VMClockXML.TimerXML()
    new_timer.setup_attrs(**timer_attrs)
    clock_timers.append(new_timer)
    vm_clock.timers = clock_timers
    vmxml.clock = vm_clock


def assemble_hyperv_feature_list(feature_list, params, test):
    """
    Assemble a hyperv feature dict

    :param feature_list: list, name list of hyperv features
    :param params: dict, test parameters
    :param test: test object

    :return: dict, hyperv features
    """
    hyperv_attr = {}
    for one_feature in feature_list:
        if one_feature == 'spinlocks':
            hyperv_attr.update({one_feature: {'state': 'on', 'retries': '4096'}})
        elif one_feature == 'stimer':
            hyperv_attr.update({one_feature: {'state': 'on', 'direct': {'state': 'on'}}})
        elif one_feature == 'vendor_id':
            hyperv_attr.update({one_feature: {'state': 'on', 'value': 'KVM Hv'}})
        else:
            hyperv_attr.update({one_feature: {'state': 'on'}})
    if params.get('features_from_domcap') == 'not in':
        feature_names = eval(params.get('all_possible_hyperv_features'))
        difference_set = set(feature_names).difference(set(feature_list))
        test.log.debug("Get hyperv features gap: %s", difference_set)
        if difference_set:
            hyperv_attr.update({list(difference_set)[0]: {'state': 'on'}})
        else:
            test.cancel("Can not find an unsupported hyperv feature on the host.")
    test.log.debug("Get assembled hyperv features:\n%s", hyperv_attr)
    return hyperv_attr


def get_expected_hyperv_values_in_qemu_line(feature_name):
    """
    Get expected hyperv feature values in qemu command line

    :param feature_name: str, the hyperv feature name
    :return: str, expected qemu command line for given feature
    """
    feature_name_value_mapping = {'hv-spinlocks': 'hv-spinlocks=0x1000',
                                  'hv-stimer': 'hv-stimer=on,hv-stimer-direct=on',
                                  'hv-vendor-id': 'hv-vendor-id=KVM Hv'}
    if feature_name in feature_name_value_mapping:
        return feature_name_value_mapping[feature_name]
    else:
        return "%s=on" % feature_name


def run(test, params, env):
    """
    Test vm features
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    hyperv_attr = eval(params.get('hyperv_attr', '{}'))
    pmu_attr = eval(params.get('pmu_attr', '{}'))
    pvspinlock_attr = eval(params.get('pvspinlock_attr', '{}'))
    kvm_poll_control_attr = eval(params.get('kvm_poll_control_attr', '{}'))
    hidden_attr = eval(params.get('hidden_attr', '{}'))
    qemu_include = params.get('qemu_include', '')
    qemu_exclude = params.get('qemu_exclude', '')
    cmd_in_guest = params.get('cmd_in_guest')
    pkgs = params.get('pkgs')
    features_from_domcap = params.get('features_from_domcap')
    status_error = params.get('status_error') == 'yes'

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm_session = None
    try:
        # Set hyperv features if there're features to set
        if features_from_domcap:
            feature_list = get_hyperv_features_in_domcapabilities()
            if not feature_list:
                test.cancel("Can not find any supported hyperv features")
            if 'stimer' in feature_list:
                add_required_timer(vmxml, {'name': 'hypervclock', 'present': 'yes'})
            hyperv_attr = assemble_hyperv_feature_list(feature_list, params, test)
        if hyperv_attr:
            update_hyperv_features(vmxml, hyperv_attr, test)
            vmxml.sync()
            test.log.debug('Before starting, vm xml:\n%s', vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))
        # Set feature attrs
        test_attrs = [pmu_attr, pvspinlock_attr, kvm_poll_control_attr, hidden_attr]
        [vm_xml.VMXML.set_vm_features(vm_name, **fea_attr)
         for fea_attr in test_attrs if fea_attr]

        # Test vm start
        try:
            ret = virsh.start(vm_name, debug=True)
            libvirt.check_exit_status(ret, status_error)
        except exceptions.TestFail as details:
            if re.search(r"host doesn\'t support paravirtual spinlocks",
                         str(details)):
                test.cancel("This host doesn't support paravirtual spinlocks.")
            else:
                test.fail('VM failed to start:\n%s' % details)
        vm_session = vm.wait_for_login()

        if hyperv_attr and features_from_domcap == 'in':
            # Check hyperv settings in qemu command line
            expect_exist_list = []
            expect_nonexist_list = []
            for attr in hyperv_attr:
                new_attr = re.sub('_', '-', attr)
                if libvirt_version.version_compare(5, 6, 0):
                    exp_str = 'hv-' + new_attr
                else:
                    exp_str = 'hv_' + new_attr
                if hyperv_attr[attr] == 'off':
                    expect_nonexist_list.append(exp_str)
                else:
                    exp_str = get_expected_hyperv_values_in_qemu_line(exp_str)
                    expect_exist_list.append(exp_str)
            if expect_nonexist_list:
                test.log.debug("Not expected list:%s", expect_nonexist_list)
                libvirt.check_qemu_cmd_line(expect_nonexist_list, expect_exist=False)
            if expect_exist_list:
                test.log.debug("Expected list:%s", expect_exist_list)
                libvirt.check_qemu_cmd_line(expect_exist_list)

        if pmu_attr:
            libvirt.check_qemu_cmd_line('pmu=' + pmu_attr['pmu'])

        if pvspinlock_attr:
            if distro.detect().name == 'rhel' and int(distro.detect().version) < 8:
                if pvspinlock_attr['pvspinlock_state'] == 'on':
                    exp_str = r'\+kvm_pv_unhalt'
                else:
                    exp_str = r'\-kvm_pv_unhalt'
            else:
                exp_str = 'kvm-pv-unhalt=' + pvspinlock_attr['pvspinlock_state']

            libvirt.check_qemu_cmd_line(exp_str)
        if qemu_include:
            libvirt.check_qemu_cmd_line(qemu_include)
        if qemu_exclude:
            if libvirt.check_qemu_cmd_line(qemu_exclude, err_ignore=True):
                test.fail('Unexpected "%s" was found '
                          'in qemu command line' % qemu_exclude)
        if cmd_in_guest:
            if pkgs:
                install_pkgs(vm_session, pkgs, test)
            cmd_params = {'hidden_attr': hidden_attr}
            check_cmd_in_guest(cmd_in_guest, vm_session, cmd_params, test)

    finally:
        if vm_session:
            vm_session.close()
        bkxml.sync()
