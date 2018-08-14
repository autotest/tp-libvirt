import logging

from avocado.utils import process

from virttest.compat_52lts import decode_to_text as to_text


def run(test, params, env):
    """
    verify the rpm package after install
    1) run test cmd
    2) check result
    """
    check_cmd = params.get("check_cmd")

    try:
        out = to_text(process.system_output(check_cmd,
                                            ignore_status=False,
                                            shell=True))

    finally:
        logging.debug("the result is : %s" % out)
        if out:
            test.fail("verify rpm package appear out expect result: %s " % out)
