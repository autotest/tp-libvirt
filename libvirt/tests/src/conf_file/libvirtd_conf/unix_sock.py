import grp
import time
import stat
import logging
import os.path
from virttest import virsh
from virttest import utils_config
from virttest import utils_libvirtd
from autotest.client.shared import error


def run(test, params, env):
    """
    Test unix_sock_* parameter in libvird.conf.

    1) Change unix_sock_* in libvirtd.conf;
    2) Restart libvirt daemon;
    3) Check if libvirtd successfully started;
    4) Check if libvirtd socket file changed accordingly;
    """
    def mode_bits_to_str(bits):
        """
        Translate a integer returned by stat.S_IMODE() to 4-digit permission
        string.
        :param bits: A integer returned by stat.S_IMODE(), like "511".
        :return : Translated 4-digit permission string, like "0777".
        """
        ubit = bits % 8
        bits /= 8
        gbit = bits % 8
        bits /= 8
        obit = bits % 8
        bits /= 8
        return "%s%s%s%s" % (bits, obit, gbit, ubit)

    def check_unix_sock(group, perms, path):
        """
        Check the validity of one libvirt socket file, including existance,
        group name, access permission and usability of virsh command.

        :param group: Expected group of the file.
        :param perms: Expected permission string of the file.
        :param path: Absolute path of the target file.
        :return : True if success or False if any test fails.
        """
        mode = os.stat(path).st_mode
        gid = os.stat(path).st_gid

        # Check file exists as a socket file.
        if not stat.S_ISSOCK(mode):
            logging.error("File %s is not a socket file." % path)
            return False

        # Check file group ID.
        try:
            expected_gid = grp.getgrnam(group).gr_gid
            logging.debug('Group ID of %s is %s' % (group, expected_gid))
            if gid != expected_gid:
                logging.error('File group gid expected to be '
                              ' %s, but %s found' % (expected_gid, gid))
                return False
        except KeyError:
            logging.error('Can not find group "%s"' % group)
            return False

        # Check file permissions.
        mode_str = mode_bits_to_str(stat.S_IMODE(mode))
        logging.debug('Permission of file %s is %s' % (path, mode_str))
        # Zero padding perms to 4 digits.
        expected_perms = perms.zfill(4)
        if mode_str != expected_perms:
            logging.error('Expected file permission is %s, but %s '
                          'found' % (expected_perms, mode_str))
            return False

        # Check virsh connection.
        uri = 'qemu+unix:///system?socket=%s' % path
        result = virsh.dom_list('--all', uri=uri)
        logging.debug('Result of virsh test run is:\n %s' % result)
        if result.exit_status:
            logging.error('Error encountered when running virsh list on '
                          'socket file %s' % path)
            return False

        # All success
        return True

    def check_all_unix_sock(group, ro_perms, rw_perms, root_path):
        """
        Check the validity of two libvirt socket files.

        :param group: Expected group of the files.
        :param ro_perms: Expected permission string of the read-only file.
        :param rw_perms: Expected permission string of the read-write file.
        :param root_path: Absolute path of the directory that target file in.
        :return : True if success or False if any test fails.
        """
        rw_path = os.path.join(root_path, 'libvirt-sock')
        logging.debug("Checking read-write socket file %s" % rw_path)
        if not check_unix_sock(group, rw_perms, rw_path):
            return False

        ro_path = os.path.join(root_path, 'libvirt-sock-ro')
        logging.debug("Checking read-only socket file %s" % ro_path)
        return check_unix_sock(group, ro_perms, ro_path)

    config = utils_config.LibvirtdConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    group = params.get('unix_sock_group', 'root')
    ro_perms = params.get('unix_sock_ro_perms', '0777')
    rw_perms = params.get('unix_sock_rw_perms', '0777')
    path = params.get('unix_sock_dir', '/var/run/libvirt')
    expected_result = params.get('expected_result', 'success')
    try:
        # Change params in libvirtd.conf
        config.unix_sock_group = group
        config.unix_sock_ro_perms = ro_perms
        config.unix_sock_rw_perms = rw_perms
        config.unix_sock_dir = path

        # Restart libvirtd to make change valid.
        if path == '/var/run/libvirt':
            restarted = libvirtd.restart()
        # Using restart() in utils_libvirtd will try to connect daemon
        # with 'virsh list'. This will fail if socket file location
        # changed. We solve this by bypassing the checking part.
        else:
            restarted = libvirtd.libvirtd.restart()

        if not restarted:
            if expected_result != 'unbootable':
                raise error.TestFail('Libvirtd is expected to be started.')
            return

        if expected_result == 'unbootable':
            raise error.TestFail('Libvirtd is not expected to be started.')

        if check_all_unix_sock(group, ro_perms, rw_perms, path):
            if expected_result == 'fail':
                raise error.TestFail('Expected fail, but check passed.')
        else:
            if expected_result == 'success':
                raise error.TestFail('Expected success, but check failed.')
    finally:
        config.restore()
        libvirtd.restart()
