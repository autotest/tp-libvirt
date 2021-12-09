======================================
Review comment summary for tp-libvirt
======================================

======================================================================
Overview
======================================================================

This doc summarize the most common review comments in tp-libvirt open source project.

On one hand it may serve as a check list for reviewers but also for coders to make sure their PR meets expected quality level before submission.

======================================================================
Goal
======================================================================

1. Provide one place to accumulate knowledge.
2. Provide help to coders in expected coding or style format for tp-libvirt.
3. Provide a reference for reviewers.

======================================================================
Common issues
======================================================================

More common issues during reviewing can be categorized as below.

-------------
Format
-------------
- cfg file

 - trailing whitespaces
 - indent alignment
 - variable reusable
 - variable name (upper or lower case letter)
 - module import order (better similar module stay together)
 - spelling errors

- py file:

 - imported module one blank line
 - missing doc comments
 - doc comments need one empty line between comment and parameter
 - doc comments start with #<space>'
 - comments include multiple lines, need care ','
 - indent issue
 - trailing whitespace
 - unused variable
 - remove comment code (#...)
 - comment upper and lower letter issues
 - logging.debug("Find snapshots: %s", snap_names)
 - spelling errors
 - escape sequences, should prepend regex patterns with 'r'

-----------------
Coding
-----------------
- variable name issue:

 - name should be meaningful

- variable declaration:

 - should be defined on the very top (avoids local redefinition and undefined names during tear down in case of error)

- deprecated method:

 - test.skip (remove raise)

 - autotest.client import utils

- result assert

 - libvirt_vm.check_exit_status

- comments

 - if a test is added or modified and there's a comment - usually for the 'run' function - then we should make sure
   the comment still is valid
 
- resource cleanup

 - vm_connection_session close(exception gracefully close)

 - vm_backup.sync() called before any change

- variable not definition

 - extreme situation: variable not definition such as conditional

- parameters in method is not usable

 - Use autoflake to remove unused parameters

- list index out of range

 - make sure that your list index

- duplicate code avoidance

 - when code was called twice, better package them into one function

 - depending on how specific the function is to a test case, it should live

   - as top-level function in the test script, if it's not needed in other test scripts

   - as a helper function in modules below tp-libvirt/provider, if it's a function that needs to be used in several test scripts in tp-libvirt; the module structure should be similar to the structure of the test cases or similar to how avocado-vt organizes its modules

   - in avocado-vt, if it's a function that's generally useful for various tests and test providers

 - these functions can be moved in time, e.g. from test script, to provider module, to avocado-vt

 - when defining a function, raising avocado exceptions directly instead of passing the `test` instance from the the run method, can make it easier to move the function later to other places

- exception handling

 - miss do assert in throwing exception

- libvirt version switches

 - instead of hardcoding libvirt version switches in the test scripts, it's preferable to add the version that's minimally supported in the test configuration file, e.g. called `func_supported_since_libvirt_ver = (7, 0, 0)` in the cfg and use in the script as `libvirt_version.is_libvirt_feature_supported(params)`.

======================================================================
Enhancement (best practice):
======================================================================
- conjunction two folders:use os.path.join(xx,xx)
- avoid too generic logging message
- not recommended to use mutable default value as an argument see <https://docs.quantifiedcode.com/python-anti-patterns/correctness/mutable_default_value_as_argument.html>
- without timeout value in infinite loop
- use :code:`with` to open files
- either use global constants for timeouts or test :code:`params` to set values for timeouts (e.g. :code:`wait_for_loging(..., timeout=LOGIN_TIMEOUT)`);
  this way it is easier for others to tweak timeouts on slower systems
- wait don't sleep: try to avoid using time.sleep(...); instead try waiting for a condition to hold (utils_misc.wait_for) - this speeds up testing and enables us to use larger timeout
  values without necessarily increasing test duration
- make sure this is run before sending patch::

    inspekt checkall --disable-style E501,E265,W601,E402,E722,E741 --no-license-check <test-script-name>.py
