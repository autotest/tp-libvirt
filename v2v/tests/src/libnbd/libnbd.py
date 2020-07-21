import nbd


def run(test, params, env):
    """
    Basic libnbd tests
    """
    checkpoint = params.get('checkpoint')

    def test_get_size():
        """
        libnbd get_size test

        1) create a nbd server by nbdkit
        2) check the image size by get_size
        3) check the image conent by pwrite and pread
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

    if checkpoint == 'get_size':
        test_get_size()
    elif checkpoint == 'is_zero':
        test_is_zero()
    else:
        test.error('Not found testcase: %s' % checkpoint)
