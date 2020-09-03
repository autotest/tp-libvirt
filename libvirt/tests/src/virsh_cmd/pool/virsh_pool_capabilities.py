import logging

from avocado.utils import process

from virttest import libvirt_vm
from virttest import virsh
from virttest.libvirt_xml import pool_capability_xml

from virttest import libvirt_version


def run(test, params, env):
    """
    Test the command virsh capabilities

    (1) Call virsh pool-capabilities
    (2) Call virsh pool-capabilities with an unexpected option
    """
    def compare_poolcapabilities_xml(source):
        """
        Compare new output of pool-capability with the standard one

        (1) Dict the new pool capability XML
        (2) Compare with the standard XML dict
        """
        cap_xml = pool_capability_xml.PoolcapabilityXML()
        cap_xml.xml = source
        connect_uri = libvirt_vm.normalize_connect_uri(params.get("connect_uri", 'default'))

        # Check the pool capability xml
        pool_capa = cap_xml.get_pool_capabilities()
        logging.debug(pool_capa)
        pool_type_list = ['dir', 'fs', 'netfs', 'logical', 'disk', 'iscsi', 'iscsi-direct',
                          'scsi', 'mpath', 'rbd', 'sheepdog', 'gluster', 'zfs', 'vstorage']
        for pooltype in pool_capa.keys():
            if pooltype not in pool_type_list:
                test.fail("'%s' is not expected in pool-capability" % (pooltype))
        pool_type_info_dict = {'dir': {'pool_default_format_name': [],
                                       'raw': ['none', 'raw', 'dir', 'bochs', 'cloop', 'dmg', 'iso', 'vpc', 'vdi',
                                               'fat', 'vhd', 'ploop', 'cow', 'qcow', 'qcow2', 'qed', 'vmdk']},
                               'fs': {'auto': ['auto', 'ext2', 'ext3', 'ext4', 'ufs', 'iso9660', 'udf', 'gfs', 'gfs2',
                                               'vfat', 'hfs+', 'xfs', 'ocfs2', 'vmfs'],
                                      'raw': ['none', 'raw', 'dir', 'bochs', 'cloop', 'dmg', 'iso', 'vpc', 'vdi',
                                              'fat', 'vhd', 'ploop', 'cow', 'qcow', 'qcow2', 'qed', 'vmdk']},
                               'netfs': {'auto': ['auto', 'nfs', 'glusterfs', 'cifs'],
                                         'raw': ['none', 'raw', 'dir', 'bochs', 'cloop', 'dmg', 'iso', 'vpc', 'vdi',
                                                 'fat', 'vhd', 'ploop', 'cow', 'qcow', 'qcow2', 'qed', 'vmdk']},
                               'logical': {'lvm2': ['unknown', 'lvm2'], 'vol_default_format_name': []},
                               'disk': {'unknown': ['unknown', 'dos', 'dvh', 'gpt', 'mac', 'bsd', 'pc98', 'sun',
                                                    'lvm2'],
                                        'none': ['none', 'linux', 'fat16', 'fat32', 'linux-swap', 'linux-lvm',
                                                 'linux-raid', 'extended']},
                               'iscsi': {'pool_default_format_name': [], 'vol_default_format_name': []},
                               'iscsi-direct': {'pool_default_format_name': [], 'vol_default_format_name': []},
                               'scsi': {'pool_default_format_name': [], 'vol_default_format_name': []},
                               'mpath': {'pool_default_format_name': [], 'vol_default_format_name': []},
                               'rbd': {'pool_default_format_name': []},
                               'sheepdog': {'pool_default_format_name': [], 'vol_default_format_name': []},
                               'gluster': {'pool_default_format_name': [],
                                           'raw': ['none', 'raw', 'dir', 'bochs', 'cloop', 'dmg', 'iso', 'vpc',
                                                   'vdi', 'fat', 'vhd', 'ploop', 'cow', 'qcow', 'qcow2', 'qed',
                                                   'vmdk']},
                               'zfs': {'pool_default_format_name': [], 'vol_default_format_name': []},
                               'vstorage': {'pool_default_format_name': [],
                                            'raw': ['none', 'raw', 'dir', 'bochs', 'cloop', 'dmg', 'iso', 'vpc',
                                                    'vdi', 'fat', 'vhd', 'ploop', 'cow', 'qcow', 'qcow2', 'qed',
                                                    'vmdk']}}

        #Check the pool capability information
        if pool_capa != pool_type_info_dict:
            test.fail('Unexpected pool information support occured,please check the information by manual')

    # Run test case
    option = params.get("virsh_pool_cap_options")
    try:
        output = virsh.pool_capabilities(option, ignore_status=False, debug=True)
        status = 0   # good
    except process.CmdError:
        status = 1   # bad
        output = ''
    status_error = params.get("status_error")
    if status_error == "yes":
        if status == 0:
            if not libvirt_version.version_compare(5, 0, 0):
                test.fail("Command 'virsh pool-capabilities %s'"
                          "doesn't support in this libvirt version" % option)
            else:
                test.fail("Command 'virsh pool-capabilities %s'"
                          "succeeded (incorrect command)" % option)
    elif status_error == "no":
        compare_poolcapabilities_xml(output)
        if status != 0:
            test.fail("Command 'virsh capabilities %s' failed"
                      "(correct command)" % option)
