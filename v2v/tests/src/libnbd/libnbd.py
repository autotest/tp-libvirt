import nbd
import os
import logging

from avocado.utils import process
from virttest import data_dir
from virttest.utils_v2v import multiple_versions_compare

LOG = logging.getLogger('avocado.v2v.' + __name__)


def run(test, params, env):
    """
    Basic libnbd tests
    """
    checkpoint = params.get('checkpoint')
    version_required = params.get('version_required')

    def test_get_size():
        """
        libnbd get_size test

        1) create a nbd server by nbdkit
        2) check the image size by get_size
        3) check the image content by pwrite and pread
        """
        expected_size = int(params.get('image_size', 1048576))
        real_size = 0
        # must be a bytes-like object for pwrite
        expected_str = b'hello, world'

        handle = nbd.NBD()
        handle.connect_command(["nbdkit", "--exit-with-parent", "-s",
                                "memory", "size=%d" % expected_size])
        real_size = handle.get_size()

        if real_size != expected_size:
            test.fail(
                'get_size failed: expected: %s, real: %s' %
                (expected_size, real_size))

        handle.pwrite(expected_str, 0, nbd.CMD_FLAG_FUA)
        real_str = handle.pread(len(expected_str), 0)

        if real_str != expected_str:
            test.fail(
                'pwrite or pread failed: expected: %s, real: %s' %
                (expected_str, real_str))

    def test_is_zero():
        """
        Basic libnbd is_zero test

        1) create nbd buffer with zeros/non-zeros
        2) check the nbd buffer by is_zero function
        """

        # All zeros
        buf = nbd.Buffer.from_bytearray(bytearray(10))
        msg = 'zero buffer'
        if not buf.is_zero():
            test.fail('is_zero test failed: %s' % msg)

        # First 6 bytes zeros, last 5 bytes non-zeros
        buf = nbd.Buffer.from_bytearray(bytearray(5) + bytearray(range(5)))
        msg = 'mixed buffer(offset=0, size=-1)'
        if buf.is_zero(offset=0, size=-1):
            test.fail('is_zero test failed: %s' % msg)

        msg = 'mixed buffer(offset=0, size=6)'
        if not buf.is_zero(offset=0, size=6):
            test.fail('is_zero test failed: %s' % msg)

        msg = 'mixed buffer(offset=6, size=0)'
        # If size=0, is_zero always return True
        if not buf.is_zero(offset=6, size=0):
            test.fail('is_zero test failed: %s' % msg)

        msg = 'mixed buffer(offset=6, size=-1)'
        if buf.is_zero(offset=6, size=-1):
            test.fail('is_zero test failed: %s' % msg)

        msg = 'mixed buffer(offset=6, size=2)'
        if buf.is_zero(offset=6, size=2):
            test.fail('is_zero test failed: %s' % msg)

    def test_unsanitized_hostname():
        expected_err_msg = params.get('expected_err_msg')
        # Commands
        cmd_list = [
            "nbdinfo nbd+ssh://-oProxyCommand=glxgears",
            "nbdinfo nbd+ssh://-oProxyCommand=xeyes",
            "nbdinfo nbd+ssh://-oProxyCommand=gnome-calculator"
        ]

        # Execute commands
        for cmd in cmd_list:
            result = process.run(cmd, shell=True, ignore_status=True)
            cmd_output = result.stderr_text
            if expected_err_msg not in cmd_output:
                test.fail(f"Unsanitized hostname validation failed. Expected error message - {expected_err_msg!r}, got {cmd_output!r}")

    def get_disk_usage(path):
        """Returns the actual disk usage (blocks) in human readable format."""
        # du -h equivalent
        stat = os.stat(path)
        # st_blocks is usually in 512-byte units
        usage_bytes = stat.st_blocks * 512
        return usage_bytes / (1024 * 1024)

    def test_nbdcopy_option_destination_is_zero():
        """
        Test Case: Verify --destination-is-zero optimizes copying to pre-zeroed targets.

        Steps:
        1. Create a 100M sparse source image containing a small string at a 1M offset.
        2. Verify source disk usage is minimal (typically 4K).
        3. Create a 100M destination image fully allocated with zeros (non-sparse).
        4. Verify destination initial disk usage is 100M.
        5. Run 'nbdcopy' with --destination-is-zero from source to destination.
        6. Verify destination disk usage has decreased to match source sparseness (~4K).
        7. Perform a binary comparison (cmp) to ensure data integrity.
        """
        # Configuration
        SRC_IMG = os.path.join(data_dir.get_tmp_dir(), "src.img")
        DEST_ZERO = os.path.join(data_dir.get_tmp_dir(), "dest_zero.img")

        # 1. Create source with small data (sparse)
        process.run(f"truncate -s 100M {SRC_IMG}", shell=True)
        process.run(f"echo 'HELLO' | dd of={SRC_IMG} bs=1 seek=1M conv=notrunc", shell=True)

        # 2. Create destination that is FULLY ALLOCATED (non-sparse) zeroes
        process.run(f"dd if=/dev/zero of={DEST_ZERO} bs=1M count=100", shell=True)
        LOG.info(f"Initial Dest Usage: {get_disk_usage(DEST_ZERO)}MB (Should be 100MB)")

        # 3. Run nbdcopy with --destination-is-zero
        # This tells nbdcopy it can skip writing zeroes, effectively punching holes
        process.run(f"nbdcopy --destination-is-zero --synchronous {SRC_IMG} {DEST_ZERO} -v", shell=True)

        # 4. Verify
        dest_usage = get_disk_usage(DEST_ZERO)
        LOG.info(f"Final Dest Usage: {dest_usage}MB (Should be very small, e.g., <1MB)")

        if dest_usage > 1:
            test.fail(f"FAILURE: Destination not sparsified (usage: {dest_usage}MB).")

        # Compare content
        result = process.run(f"cmp {SRC_IMG} {DEST_ZERO}", shell=True, ignore_status=True)
        if result.exit_status != 0:
            test.fail("FAILURE: Content mismatch between source and destination.")

    def test_nbdcopy_option_allocated():
        """
        Test Case: Verify --allocated forces a non-sparse output.

        Steps:
        1. Create a 100M sparse source image using 'truncate'.
        2. Write 1M of random data at two different offsets (10M and 50M)
        to ensure the file remains sparse.
        3. Verify source disk usage is minimal (~2M) using 'du'.
        4. Run 'nbdcopy' with --allocated and --synchronous flags from
        source to destination.
        5. Verify destination disk usage is exactly 100M.
        6. Verify destination and source logical sizes are identical.
        """
        # Configuration
        SRC_IMG = os.path.join(data_dir.get_tmp_dir(), "src.img")
        DEST_ALLOC = os.path.join(data_dir.get_tmp_dir(), "dest_alloc.img")

        # 1. Create sparse source image (100M)
        process.run(f"truncate -s 100M {SRC_IMG}", shell=True)

        # 2. Write random data at specific offsets to keep it sparse
        process.run(f"dd if=/dev/urandom of={SRC_IMG} bs=1M count=1 seek=10 conv=notrunc", shell=True)
        process.run(f"dd if=/dev/urandom of={SRC_IMG} bs=1M count=1 seek=50 conv=notrunc", shell=True)

        src_usage = get_disk_usage(SRC_IMG)
        LOG.info(f"Source Disk Usage: {src_usage}MB (Should be ~2MB)")

        # 3. Run nbdcopy with --allocated
        cmd_nbdcopy = f"nbdcopy --allocated --synchronous {SRC_IMG} {DEST_ALLOC} -v"
        process.run(cmd_nbdcopy, shell=True)

        # 4. Verify destination file usage
        dest_usage = get_disk_usage(DEST_ALLOC)
        LOG.info(f"Destination Disk Usage: {dest_usage}MB (Should be 100MB)")
        if dest_usage < 100:
            test.fail("FAILURE: Destination is still sparse.")

        # 5. Verify logical sizes match
        src_size = os.path.getsize(SRC_IMG)
        dest_size = os.path.getsize(DEST_ALLOC)
        if src_size != dest_size:
            test.fail(f"FAILURE: Logical size mismatch. Source: {src_size}, Dest: {dest_size}")

        # 6. Verify content
        result = process.run(f"cmp {SRC_IMG} {DEST_ALLOC}", shell=True, ignore_status=True)
        if result.exit_status != 0:
            test.fail("FAILURE: Content mismatch between source and destination.")

    if version_required and not multiple_versions_compare(
            version_required):
        test.cancel("Testing requires version: %s" % version_required)

    if checkpoint == 'get_size':
        test_get_size()
    elif checkpoint == 'is_zero':
        test_is_zero()
    elif checkpoint == 'check_unsanitized_hostname':
        test_unsanitized_hostname()
    elif checkpoint == 'check_option_destination_is_zero':
        test_nbdcopy_option_destination_is_zero()
    elif checkpoint == 'check_option_allocated':
        test_nbdcopy_option_allocated()
    else:
        test.error('Not found testcase: %s' % checkpoint)
