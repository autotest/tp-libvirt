======================================
Review comment summary for tp-libvirt
======================================

======================================================================
Overview
======================================================================

This doc summarize the most common review comments in tp-libvirt open source project.

For beginners or practice executor, it may provide some self-check before submitting to normal review in github.

======================================================================
Goal
======================================================================

1. Provide one place to accumulate knowledge.
2. Provide some help to beginners or executors in coding or style format for tp-libvirt.

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

 - when code was called twice, better package them into one method

- exception handling

 - miss do assert in throwing exception

======================================================================
Enhancement (best practice):
======================================================================
- conjunction two folders:use os.path.join(xx,xx)
- avoid too generic logging message
- not recommended to use mutable default value as an argument see <https://docs.quantifiedcode.com/python-anti-patterns/correctness/mutable_default_value_as_argument.html>
- without timeout value in infinite loop
- use `with` to open files
- either use global constants for timeouts or test `params` to set values for timeouts (e.g. `wait_for_loging(..., timeout=LOGIN_TIMEOUT)`);
  this way it is easier for others to tweak timeouts on slower systems
- make sure this is run before sending patch::

    inspekt checkall --disable-style E501,E265,W601,E402,E722,E741 --no-license-check <test-script-name>.py
