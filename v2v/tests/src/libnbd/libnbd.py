import nbd


def run(test, params, env):
    """
    Basic libnbd test

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
