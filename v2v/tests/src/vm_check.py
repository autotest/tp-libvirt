import re
import logging
import commands
from autotest.client.shared import error
from virttest import utils_v2v, virsh


def run(test, params, env):
    """
    Check VM after conversion
    """
    target = params.get('target')
    hypervisor = params.get('hypervisor')
    hostname = params.get('hostname')
    vpx_dc = params.get('vpx_dc')
    esx_ip = params.get('esx_ip')
    vm_name = params.get('main_vm')

    check_obj = utils_v2v.VMCheck(test, params, env)

    try:
        logging.info("Check guest os info")
        os_info = check_obj.get_vm_os_info()
        os_vendor = check_obj.get_vm_os_vendor()
        if os_vendor == 'Red Hat':
            os_version = os_info.split()[6]
        else:
            raise error.TestFail("Only RHEL is supported now.")

        logging.info("Check guest kernel after conversion")
        kernel_version = check_obj.get_vm_kernel()
        if re.search('xen', kernel_version):
            raise error.TestFail("FAIL")
        else:
            logging.info("SUCCESS")

        logging.info("Check parted info after conversion")
        parted_info = check_obj.get_vm_parted()
        if os_version != '3':
            if re.findall('/dev/vd\S+', parted_info):
                logging.info("SUCCESS")
            else:
                raise error.TestFail("FAIL")

        logging.info("Check virtio_net module in modprobe conf")
        modprobe_conf = check_obj.get_vm_modprobe_conf()
        if not re.search('No such file', modprobe_conf):
            virtio_mod = re.findall(r'(?m)^alias.*virtio', modprobe_conf)
            net_blk_mod = re.findall(r'(?m)^alias\s+scsi|(?m)^alias\s+eth',
                                     modprobe_conf)
            if len(virtio_mod) == len(net_blk_mod):
                logging.info("SUCCESS")
            else:
                raise error.TestFail("FAIL")

        logging.info("Check virtio module")
        modules = check_obj.get_vm_modules()
        if os_version == '3':
            if re.search("e1000|^ide", modules):
                logging.info("SUCCESS")
            else:
                raise error.TestFail("FAIL")
        elif re.search("virtio", modules):
            logging.info("SUCCESS")
        else:
            raise error.TestFail("FAIL")

        logging.info("Check virtio pci devices")
        pci = check_obj.get_vm_pci_list()
        if os_version != '3':
            if (re.search('[Vv]irtio network', pci) and
                    re.search('[Vv]irtio block', pci)):
                if target == "ovirt":
                    logging.info("SUCCESS")
                elif (target != "ovirt" and
                      re.search('[Vv]irtio memory', pci)):
                    logging.info("SUCCESS")
                else:
                    raise error.TestFail("FAIL")
            else:
                raise error.TestFail("FAIL")

        logging.info("Check in /etc/rc.local")
        rc_output = check_obj.get_vm_rc_local()
        if re.search('^[modprobe|insmod].*xen-vbd.*', rc_output):
            raise error.TestFail("FAIL")
        else:
            logging.info("SUCCESS")

        logging.info("Check vmware tools")
        if check_obj.has_vmware_tools() is False:
            logging.info("SUCCESS")
        else:
            raise error.TestFail("FAIL")

        logging.info("Check tty")
        tty = check_obj.get_vm_tty()
        if re.search('[xh]vc0', tty):
            raise error.TestFail("FAIL")
        else:
            logging.info("SUCCESS")

        logging.info("Check video")
        video_model = ""
        if hypervisor == 'kvm':
            # dump VM XML
            cmd = "virsh dumpxml %s |grep -A 3 '<video>'" % vm_name
            status, output = commands.getstatusoutput(cmd)
            # get remote session
            if status:
                raise error.TestError(vm_name, output)

            video_type = re.search("type='[a-z]*'", output)
            if video_type:
                video_model = eval(video_type.group(0).split('=')[1])

        video = check_obj.get_vm_video()
        if target == 'ovirt':
            if re.search('qxl', video):
                logging.info("SUCCESS")
            else:
                raise error.TestFail("FAIL")
        else:
            if re.search('el7', kernel_version):
                if 'cirrus' in output:
                    if re.search('kms', video):
                        logging.info("SUCCESS")
                    else:
                        raise error.TestFail("FAIL")
                else:
                    if re.search(video_model, video):
                        logging.info("SUCCESS")
                    else:
                        raise error.TestFail("FAIL")
            else:
                if re.search(video_model, video):
                    logging.info("SUCCESS")
                else:
                    raise error.TestFail("FAIL")
    finally:
        del check_obj
