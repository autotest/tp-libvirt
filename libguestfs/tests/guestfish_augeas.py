"""
This file is used to run autotest cases related to augeas
"""
import re
import logging

from virttest import utils_test


def prepare_image(test, params):
    """
    1) Create a image
    2) Create file system on the image
    """

    params["image_path"] = utils_test.libguestfs.preprocess_image(params)

    if not params.get("image_path"):
        test.fail("Image could not be created for some reason")

    gf = utils_test.libguestfs.GuestfishTools(params)
    status, output = gf.create_fs()
    if status is False:
        gf.close_session()
        test.fail(output)
    gf.close_session()


def test_aug_clear(test, vm, params):
    """
    Clear augeas path

    1) Create a new augeas handle
    2) Set the home directory of root user to /root
    3) Clear the home directory of root user
    4) Check if the path have been cleared
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0 ")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_set_result = gf.aug_set("/files/etc/passwd/root/home", "/root")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/passwd/root/home to /root. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/passwd/root/home to /root successfully")

    aug_clear_result = gf.aug_clear("/files/etc/passwd/root/home")
    if aug_clear_result.exit_status:
        gf.close_session()
        test.fail("Can not clean /files/etc/passwd/root/home. GSERROR_MSG: %s" % aug_clear_result)
    logging.info("Clear augeas /files/etc/passwd/root/home successfully")

    aug_get_result = gf.aug_get("/files/etc/passwd/root/home")
    if not aug_get_result.exit_status:
        gf.close_session()
        test.fail("The home directory of root user should be cleared after aug-clear")
    logging.info("Clean the home directory of root user successfully")
    gf.close_session()


def test_aug_close(test, vm, params):
    """
    Close the current augeas handle and free up any resources used by it.
    After calling this, you have to call "aug_init" again before you can use
    any other augeas functions.

    1) Create a new augeas handle
    2) Close the current augeas handle
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_close_result = gf.aug_close()
    if aug_close_result.exit_status:
        gf.close_session()
        test.fail("Can not close augeas handle. GSERROR_MSG: %s" % aug_close_result)
    logging.info("Close augeas handle successfully")
    gf.close_session()


def test_aug_defnode(test, vm, params):
    """
    Defines a variable "name" whose value is the result of evaluating
    "expr".

    1) Create a new augeas handle
    2) Define an augeas node
    3) Check the value of the node
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_defnode_result = gf.aug_defnode("node", "/files/etc/passwd/test/uid", "9999")
    if aug_defnode_result.exit_status:
        gf.close_session()
        test.fail("Can not define node. GSERROR_MSG: %s" % aug_defnode_result)
    logging.info("Define node successfully")

    aug_defnode_result = gf.aug_defnode("node", "/files/etc/passwd/test/gid", "9999")
    if aug_defnode_result.exit_status:
        gf.close_session()
        test.fail("Can not define node /files/etc/passwd/test/gid. GSERROR_MSG: %s" % aug_defnode_result)
    logging.info("Define node /files/etc/passwd/test/gid successfully")

    aug_ls_result = gf.aug_ls("/files/etc/passwd/test")
    if aug_ls_result.exit_status:
        gf.close_session()
        test.fail("Can not list augeas nodes under /files/etc/passwd/test. GSERROR_MSG: %s" % aug_ls_result)
    logging.info("List augeas nodes under /files/etc/passwd/test successfully")

    if aug_ls_result.stdout.strip('\n') != '/files/etc/passwd/test/gid\n/files/etc/passwd/test/uid':
        gf.close_session()
        test.fail("The node value is not correct: %s" % aug_ls_result.stdout)
    logging.info("The node value is correct")
    gf.close_session()


def test_aug_defvar(test, vm, params):
    """
    Define an augeas variable

    1) Create a new augeas handle
    2) Define an augeas variable
    3) Check the value of the variable
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_defvar_result = gf.aug_defvar("test", "'This is a test'")
    if aug_defvar_result.exit_status:
        gf.close_session()
        test.fail("Can not define variable. GSERROR_MSG: %s" % aug_defvar_result)
    logging.info("Define variable successfully")

    aug_get_result = gf.aug_get("/augeas/variables/test")
    if aug_get_result.exit_status:
        gf.close_session()
        test.fail("Can not look up the value of /augeas/variables/test. GSERROR_MSG:%s" % aug_get_result)
    logging.info("Look up the value of /augeas/variables/test successfully")

    if aug_get_result.stdout.strip('\n') != 'This is a test':
        gf.close_session()
        test.fail("The variable value is not correct %s != This is a test" % aug_get_result.stdout.strip('\n'))
    logging.info("The variable value is correct")
    gf.close_session()


def test_aug_set_get(test, vm, params):
    """
    Look up the value of an augeas path

    1) Create a new augeas handle
    2) Set a new augeas node
    3) Get the new augeas node
    4) Check the value of the augeas path
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_set_result = gf.aug_set("/files/etc/passwd/root/password", "9999")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/passwd/root/password to 9999. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/passwd/root/password to 9999 successfully")

    aug_set_result = gf.aug_set("/files/etc/passwd/root/home", "/root")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/passwd/root/home to /root. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/passwd/root/home to /root successfully")

    aug_get_result_password = gf.aug_get("/files/etc/passwd/root/password")
    if aug_get_result_password.exit_status:
        gf.close_session()
        test.fail("Can not get the value of /files/etc/passwd/root/password. GSERROR_MSG: %s" % aug_get_result_password)
    logging.info("Get the value of /files/etc/passwd/root/password successfully")

    aug_get_result_home = gf.aug_get("/files/etc/passwd/root/home")
    if aug_get_result_home.exit_status:
        gf.close_session()
        test.fail("Can not get the value of /files/etc/passwd/root/home. GSERROR_MSG: %s" % aug_get_result_home)
    logging.info("Get the value of /files/etc/passwd/root/home successfully")

    if aug_get_result_password.stdout.strip('\n') != "9999" or aug_get_result_home.stdout.strip('\n') != '/root':
        gf.close_session()
        test.fail("The value of /files/etc/passwd/root/password and /files/etc/passwd/root/home is not correct. root password %s != 9999, root home %s != /root" % (aug_get_result_password.stdout.strip('\n'), aug_get_result_home.stdout.strip('\n')))
    logging.info("The value of /files/etc/passwd/root/password and /files/etc/passwd/root/home is correct")
    gf.close_session()


def test_aug_init(test, vm, params):
    """
    Create a new augeas handle

    1) Create a new augeas handle and set the flag to 0
    2) Create a new augeas handle and set the flag to 1
    3) Create a new augeas handle and set the flag to 8
    4) Create a new augeas handle and set the flag to 16
    5) Create a new augeas handle and set the flag to 32
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle with flag = 0. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle with flag = 0 successfully")

    aug_init_result = gf.aug_init("/", "1")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle with flag = 1. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle with flag = 1 successfully")

    aug_init_result = gf.aug_init("/", "8")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle with flag = 8. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle with flag = 8 successfully")

    aug_init_result = gf.aug_init("/", "16")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle with flag = 16. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle with flag = 16 successfully")

    aug_init_result = gf.aug_init("/", "32")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle with flag = 32. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle with flag = 32 successfully")
    gf.close_session()


def test_aug_insert(test, vm, params):
    """
    Look up the value of an augeas path

    1) Create a new augeas handle
    2) Set augeas path to value
    3) Insert a sibling augeas node
    4) Check the status of the new insert node
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    mkdir_p_result = gf.mkdir_p('/usr/share/augeas/lenses/dist')
    if mkdir_p_result.exit_status:
        gf.close_session()
        test.fail("Can not create directory /usr/share/augeas/lenses/dist. GSERROR_MSG: %s" % mkdir_p_result)
    logging.info("Create directory /usr/share/augeas/lenses/dist successfully")

    mkdir_result = gf.mkdir('/etc')
    if mkdir_result.exit_status:
        gf.close_session()
        test.fail("Can not create directory /etc. GSERROR_MSG: %s" % mkdir_result)
    logging.info("Create directory /etc successfully")

    upload_result = gf.upload('/usr/share/augeas/lenses/dist/passwd.aug', '/usr/share/augeas/lenses/dist/passwd.aug')
    if upload_result.exit_status:
        gf.close_session()
        test.fail("Can not upload file /usr/share/augeas/lenses/dist/passwd.aug. GSERROR_MSG: %s" % upload_result)
    logging.info("upload file /usr/share/augeas/lenses/dist/passwd.aug successfully")

    upload_result = gf.upload('/etc/passwd', '/etc/passwd')
    if upload_result.exit_status:
        gf.close_session()
        test.fail("Can not upload file /etc/passwd. GSERROR_MSG: %s" % upload_result)
    logging.info("upload file /etc/passwd successfully")

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_insert_result = gf.aug_insert("/files/etc/passwd/root/name", "testbefore", "true")
    if aug_insert_result.exit_status:
        gf.close_session()
        test.fail("Can not insert testbefore before /files/etc/passwd/root/name. GSERROR_MSG: %s" % aug_insert_result)
    logging.info("Insert testbefore before /files/etc/passwd/root/name successfully")

    aug_insert_result = gf.aug_insert("/files/etc/passwd/root/name", "testafter", "false")
    if aug_insert_result.exit_status:
        gf.close_session()
        test.fail("Can not insert testafter after /files/etc/passwd/root/name. GSERROR_MSG: %s" % aug_insert_result)
    logging.info("Insert testafter after /files/etc/passwd/root/name successfully")

    command_result = gf.inner_cmd("aug-match /files/etc/passwd/root/* |egrep 'name|test'")
    if command_result.exit_status:
        gf.close_session()
        test.fail("Failed to run the command. GSERROR_MSG: %s" % command_result)

    if command_result.stdout.strip('\n') != '/files/etc/passwd/root/testbefore\n/files/etc/passwd/root/name\n/files/etc/passwd/root/testafter':
        gf.close_session()
        test.fail("The match results is not correct. GSERROR_MSG: %s" % command_result.stdout)
    gf.close_session()


def test_aug_ls(test, vm, params):
    """
    List augeas nodes under augpath

    1) Create a new augeas handle
    2) Create two new nodes
    3) List the two new nodes
    4) Check the results
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_set_result = gf.aug_set("/files/etc/passwd/root", "0")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/passwd/root to 0. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/passwd/root to 0 successfully")

    aug_set_result = gf.aug_set("/files/etc/passwd/mysql", "1")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/passwd/mysql to 1. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/passwd/mysql to 1 successfully")

    aug_ls_result = gf.aug_ls("/files/etc/passwd")
    if aug_ls_result.exit_status:
        gf.close_session()
        test.fail("Can not list path /files/etc/passwd. GSERROR_MSG: %s" % aug_ls_result)
    logging.info("List path /files/etc/passwd successfully")

    if aug_ls_result.stdout.strip('\n') != '/files/etc/passwd/mysql\n/files/etc/passwd/root':
        gf.close_session()
        test.fail("aug-ls list the wrong results. GSERROR_MSG: %s" % aug_ls_result.stdout)
    logging.info("aug-ls list the right results")

    aug_ls_result = gf.aug_ls("/files/etc/passwd/")
    if not aug_ls_result.exit_status:
        gf.close_session()
        test.fail("aug_ls: can use aug-ls with a path that ends with /")

    aug_ls_result = gf.aug_ls("/files/etc/passwd/*")
    if not aug_ls_result.exit_status:
        gf.close_session()
        test.fail("aug_ls: can use aug-ls with a path that ends with *")

    aug_ls_result = gf.aug_ls("/files/etc/passwd/node[1]")
    if not aug_ls_result.exit_status:
        gf.close_session()
        test.fail("aug_ls: can use aug-ls with a path that ends with ]")
    gf.close_session()


def test_aug_match(test, vm, params):
    """
    List augeas nodes under augpath

    1) Create a new augeas handle
    2) Create two new nodes
    3) Match one of the two nodes
    4) Check the results
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_set_result = gf.aug_set("/files/etc/passwd/root", "0")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/passwd/root to 0. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/passwd/root to 0 successfully")

    aug_set_result = gf.aug_set("/files/etc/host/home", "1")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/host/home to 1. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/host/home to 1 successfully")

    aug_set_result = gf.aug_set("/files/etc/config/root", "2")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/config/root to 2. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/config/root to 2 successfully")

    aug_match_result = gf.aug_match("/files/etc/*/root")
    if aug_match_result.exit_status:
        gf.close_session()
        test.fail("Can not return augeas nodes which match /files/etc/*/root. GSERROR_MSG: %s" % aug_match_result)
    logging.info("Can return augeas nodes which match /files/etc/*/root successfully")

    if aug_match_result.stdout.strip('\n') != '/files/etc/passwd/root\n/files/etc/config/root':
        gf.close_session()
        test.fail("The match results is not correct. GSERROR_MSG: %s" % aug_match_result.stdout)
    gf.close_session()


def test_aug_mv(test, vm, params):
    """
    Move augeas node

    1) Create a new augeas handle
    2) Create a new node
    3) Move the node to other place
    4) Check the results
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_set_result = gf.aug_set("/files/etc/passwd/root", "0")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/passwd/root to 0. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/passwd/root to 0 successfully")

    aug_mv_result = gf.aug_mv("/files/etc/passwd/root", "/files/etc/passwd/other_none_root")
    if aug_mv_result.exit_status:
        gf.close_session()
        test.fail("Can not move /files/etc/passwd/root to /files/etc/passwd/other_none_root. GSERROR_MSG: %s" % aug_mv_result)

    aug_ls_result = gf.aug_ls("/files/etc/passwd")
    if aug_ls_result.exit_status:
        gf.close_session()
        test.fail("Can not list augeas nodes under /files/etc/passwd. GSERROR_MSG: %s" % aug_ls_result)
    logging.info("List augeas nodes under /files/etc/passwd successfully")

    if aug_ls_result.stdout.strip('\n') != '/files/etc/passwd/other_none_root':
        gf.close_session()
        test.fail("aug-mv: can not find the new node /files/etc/passwd/other_none_root")
    gf.close_session()


def test_aug_rm(test, vm, params):
    """
    Move augeas node

    1) Create a new augeas handle
    2) Create a new node
    3) Remove the node
    4) Check the results
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_set_result = gf.aug_set("/files/etc/passwd/root", "0")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/passwd/root to 0. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/passwd/root to 0 successfully")

    aug_rm_result = gf.aug_rm("/files/etc/passwd/root")
    if aug_rm_result.exit_status:
        gf.close_session()
        test.fail("Can not remove /files/etc/passwd/root. GSERROR_MSG: %s" % aug_rm_result)

    aug_ls_result = gf.aug_ls("/files/etc/passwd")
    if aug_ls_result.exit_status:
        gf.close_session()
        test.fail("Can not list augeas nodes under /files/etc/passwd. GSERROR_MSG: %s" % aug_ls_result)
    logging.info("List augeas nodes under /files/etc/passwd successfully")

    if aug_ls_result.stdout.strip('\n') == '/files/etc/passwd/root':
        gf.close_session()
        test.fail("aug-rm: failed to remove node /files/etc/passwd/root")
    gf.close_session()


def test_aug_label(test, vm, params):
    """
    Return the label from an augeas path expression

    1) Create a new augeas handle
    2) Create a new node
    3) Return the label from an augeas path expression
    4) Check the results
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_set_result = gf.aug_set("/files/etc/passwd/root", "0")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/passwd/root to 0. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/passwd/root to 0 successfully")

    aug_label_result = gf.aug_label("/files/etc/passwd/root")
    if aug_label_result.exit_status:
        gf.close_session()
        test.fail("Can not get the label of /files/etc/passwd/root. GSERROR_MSG: %s" % aug_label_result)

    if aug_label_result.stdout.strip('\n') != 'root':
        gf.close_session()
        test.fail("aug-label return the wrong lable")
    gf.close_session()


def test_aug_setm(test, vm, params):
    """
    Set multiple augeas nodes

    1) Create a new augeas handle
    2) Create multiple augeas nodes
    2) Set multiple augeas nodes
    3) Check the results
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_set_result = gf.aug_set("/files/etc/passwd/root/uid", "0")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/passwd/root/uid to 0. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/passwd/root/uid to 0 successfully")

    aug_set_result = gf.aug_set("/files/etc/passwd/mysql/uid", "1")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/passwd/mysql/uid to 1. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/passwd/mysql/uid to 1 successfully")

    aug_setm_result = gf.aug_setm("/files/etc/passwd/*", "uid", "2")
    if aug_setm_result.exit_status:
        gf.close_session()
        test.fail("Can not set multiple augeas nodes. GSERROR_MSG: %s" % aug_setm_result)

    aug_get_result_root = gf.aug_get("/files/etc/passwd/root/uid")
    if aug_get_result_root.exit_status:
        gf.close_session()
        test.fail("Can not get the value of /files/etc/passwd/root/uid. GSERROR_MSG: %s" % aug_get_result_root)

    aug_get_result_mysql = gf.aug_get("/files/etc/passwd/mysql/uid")
    if aug_get_result_mysql.exit_status:
        gf.close_session()
        test.fail("Can not get the value of /files/etc/passwd/mysql/uid. GSERROR_MSG: %s" % aug_get_result_mysql)

    if aug_get_result_root.stdout.strip('\n') != '2' or aug_get_result_mysql.stdout.strip('\n') != '2':
        gf.close_session()
        test.fail("aug-setm set the wrong value. GSERROR_MSG: root = %s, mysql = %s" % (aug_get_result_root.stdout.strip('\n'), aug_get_result_mysql.stdout.strip('\n')))
    gf.close_session()


def test_aug_load(test, vm, params):
    """
    Load files into the tree

    1) Create a new augeas handle
    2) upload files
    3) Load files into the tree
    4) Check the load results
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_ls_result = gf.aug_ls("/files/etc")
    if aug_ls_result.exit_status:
        gf.close_session()
        test.fail("Can not list augeas nodes under /files/etc. GSERROR_MSG: %s" % aug_ls_result)
    logging.info("List augeas nodes under /files/etc successfully")

    mkdir_p_result = gf.mkdir_p('/usr/share/augeas/lenses/dist')
    if mkdir_p_result.exit_status:
        gf.close_session()
        test.fail("Can not create directory /usr/share/augeas/lenses/dist. GSERROR_MSG: %s" % mkdir_p_result)
    logging.info("Create directory /usr/share/augeas/lenses/dist successfully")

    mkdir_result = gf.mkdir('/etc')
    if mkdir_result.exit_status:
        gf.close_session()
        test.fail("Can not create directory /etc. GSERROR_MSG: %s" % mkdir_result)
    logging.info("Create directory /etc successfully")

    upload_result = gf.upload('/usr/share/augeas/lenses/dist/passwd.aug', '/usr/share/augeas/lenses/dist/passwd.aug')
    if upload_result.exit_status:
        gf.close_session()
        test.fail("Can not upload file /usr/share/augeas/lenses/dist/passwd.aug. GSERROR_MSG: %s" % upload_result)
    logging.info("upload file /usr/share/augeas/lenses/dist/passwd.aug successfully")

    upload_result = gf.upload('/etc/passwd', '/etc/passwd')
    if upload_result.exit_status:
        gf.close_session()
        test.fail("Can not upload file /etc/passwd. GSERROR_MSG: %s" % upload_result)
    logging.info("upload file /etc/passwd successfully")

    aug_load_result = gf.aug_load()
    if aug_load_result.exit_status:
        gf.close_session()
        test.fail("Can not load files into the tree. GSERROR_MSG: %s" % aug_load_result)
    logging.info("Load files into tree successfully")

    aug_ls_load_result = gf.aug_ls("/files/etc")
    if aug_ls_load_result.exit_status:
        gf.close_session()
        test.fail("Can not list augeas nodes under /files/etc. GSERROR_MSG: %s" % aug_ls_load_result)
    logging.info("List augeas nodes under /files/etc successfully")

    if aug_ls_result.stdout.strip('\n') != '' or aug_ls_load_result.stdout.strip('\n') != '/files/etc/passwd':
        gf.close_session()
        test.fail("Failed to load the tree.")
    gf.close_session()


def test_aug_save(test, vm, params):
    """
    Write all pending augeas changes to disk

    1) upload files
    2) Create a new augeas handle
    3) Change the home directory of root user to /tmp/root
    4) Write the changes to disk
    5) Exit guestfish
    6) Add the image again
    7) Create a new augeas handle
    8) Get the home directory of root user
    9) Check the home directory of root user
    """

    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_point = params["mount_point"]
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    mkdir_p_result = gf.mkdir_p('/usr/share/augeas/lenses/dist')
    if mkdir_p_result.exit_status:
        gf.close_session()
        test.fail("Can not create directory /usr/share/augeas/lenses/dist. GSERROR_MSG: %s" % mkdir_p_result)
    logging.info("Create directory /usr/share/augeas/lenses/dist successfully")

    mkdir_result = gf.mkdir('/etc')
    if mkdir_result.exit_status:
        gf.close_session()
        test.fail("Can not create directory /etc. GSERROR_MSG: %s" % mkdir_result)
    logging.info("Create directory /etc successfully")

    upload_result = gf.upload('/usr/share/augeas/lenses/dist/passwd.aug', '/usr/share/augeas/lenses/dist/passwd.aug')
    if upload_result.exit_status:
        gf.close_session()
        test.fail("Can not upload file /usr/share/augeas/lenses/dist/passwd.aug. GSERROR_MSG: %s" % upload_result)
    logging.info("upload file /usr/share/augeas/lenses/dist/passwd.aug successfully")

    upload_result = gf.upload('/etc/passwd', '/etc/passwd')
    if upload_result.exit_status:
        gf.close_session()
        test.fail("Can not upload file /etc/passwd. GSERROR_MSG: %s" % upload_result)
    logging.info("upload file /etc/passwd successfully")

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_set_result = gf.aug_set("/files/etc/passwd/root/home", "/tmp/root")
    if aug_set_result.exit_status:
        gf.close_session()
        test.fail("Can not set /files/etc/passwd/root/home to /tmp/root. GSERROR_MSG: %s" % aug_set_result)
    logging.info("Set /files/etc/passwd/root/home to /tmp/root successfully")

    aug_save_result = gf.aug_save()
    if aug_save_result.exit_status:
        gf.close_session()
        test.fail("Can not save changes to disk. GSERROR_MSG: %s" % aug_save_result)
    logging.info("Save changes to disk successfully")
    gf.close_session()

    gf = utils_test.libguestfs.GuestfishTools(params)
    gf.add_drive_opts(image_path, readonly=readonly)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch. GSERROR_MSG: %s" % run_result)
    logging.info("Launch successfully")

    # mount the device
    mount_result = gf.mount_options("noatime", mount_point, "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Can not mount %s to /. GSERROR_MSG: %s" % (mount_point, mount_result))
    logging.info("mount %s to / successfully" % mount_point)

    aug_init_result = gf.aug_init("/", "0")
    if aug_init_result.exit_status:
        gf.close_session()
        test.fail("Can not create a augeas handle. GSERROR_MSG: %s" % aug_init_result)
    logging.info("Create augeas handle successfully")

    aug_get_result = gf.aug_get("/files/etc/passwd/root/home")
    if aug_get_result.exit_status:
        gf.close_session()
        test.fail("Can not get the home directory of root user. GSERROR_MSG: %s" % aug_get_result)
    logging.info("Get the home directory of root user successfully. root directory is %s" % aug_get_result.stdout.strip('\n'))

    if aug_get_result.stdout.strip('\n') != '/tmp/root':
        gf.close_session()
        test.fail("The home directory of root user is not correct")
    gf.close_session()


def run(test, params, env):
    """
    Test of built-in augeas related commands in guestfish

    1) Get parameters for test
    2) Set options for commands
    3) Run key commands:
       a. add disk or domain with readonly or not
       b. launch
       c. mount root device
    4) Run augeas APIs inside guestfish session
    5) Check results
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    if vm.is_alive():
        vm.destroy()

    operation = params.get("guestfish_function")
    testcase = globals()["test_%s" % operation]
    partition_types = params.get("partition_types")
    fs_types = params.get("fs_types")
    image_formats = params.get("image_formats")

    for image_format in re.findall(r"\w+", image_formats):
        params["image_format"] = image_format
        for partition_type in re.findall(r"\w+", partition_types):
            params["partition_type"] = partition_type
            for fs_type in re.findall(r"\w+", fs_types):
                params["fs_type"] = fs_type
                prepare_image(test, params)
                testcase(test, vm, params)
