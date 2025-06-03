import logging as log

from virttest import libvirt_version
from virttest import virsh
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.domcapability_xml import DomCapabilityXML


logging = log.getLogger("avocado." + __name__)


def compare_cpu_model_with_domcapabilities(test, params, result):
    """
    Compare the available CPUs with those returned by domcapabilities.

    :param test: the test instance
    :param params: the test parameters
    :param result: the output from hypervisor-cpu-models
    """
    xml = DomCapabilityXML()
    hc_models = [x for x in result.stdout_text.split("\n") if x != ""]
    if "--all" in params.get("options"):
        xpath = "/cpu/mode/model"
    else:
        xpath = "/cpu/mode/model[@usable='yes']"
    dc_models = [str(x.text) for x in xml.xmltreefile.findall(xpath)]
    difference = set(dc_models) ^ set(hc_models)
    if difference and difference != set(""):
        test.fail(f"Model list should be identical. Difference: {difference}")
    test.log.debug("All models were found in hypervisor-cpu-models")


def run(test, params, env):
    """
    Test command virsh hypervisor-cpu-models
    """
    options = params.get("options", "")
    status_error = "yes" == params.get("status_error", "no")

    libvirt_version.is_libvirt_feature_supported(params)

    result = virsh.hypervisor_cpu_models(options, ignore_status=True, debug=True)
    utlv.check_exit_status(result, expect_error=status_error)
    if not status_error:
        compare_cpu_model_with_domcapabilities(test, params, result)
