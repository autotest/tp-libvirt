======================================
Review comment summary for tp-libvirt
======================================

Overview:

This doc summarize the most common review comments in tp-libvirt open source project. 
For beginners or practice executor, it may provides some self-check before submitting to normal review in github. 
======================================================================
Goal:

1)Provide one place to accumulate knowledge 
2)Provide some helps to beginners or executors in coding or style format for tp-libvirt 

======================================================================
More common issues during reviewing can be categorized as below.
Format::
	--cfg file
		trailing whitespaces
		indent alignment
		variable reusable
		variable name(upper or lower case letter)
		Module import order(better similar module stay together)
		Spelling errors
	--py file:
		imported module one blank line
		miss doc comments
		doc comment need one empty line between comment and parameter
		doc comment start with #<space>'
		comments include multiple lines, need care ','
		indent issue
		trailing whitespace
		unused variable
		remove comment code(#...)
		comment upper and lower letter issues
		logging.debug("Find snapshots: %s", snap_names)
		Spelling errors

======================================================================
Coding:
	-- variable name issue:
	  	name should be meaningful
	-- depreciated method:
		test.skip(remove raise)
		autotest.client import utils
	-- result assert
		libvirt_vm.check_exit_status
	-- resource cleanup
		vm_connection_session close(exception gracefully close)
		vm_backup.sync() called before any change 
	-- variable not definition
		extreme situation: variable not definition such as conditional
	-- parameters in method is not usable
		Use autoflake to remove unused parameters
	-- list index out of range
		 make sure that your list index
	-- duplicate code avoidance
		 when code was called twice, better package them into one method
	-- exception handling
		 miss do assert in throwing exception

======================================================================
Enhancement(Best practice):
	-- conjunction two folders:use os.path.join(xx,xx)
	-- avoid too generic logging message
	-- not recommended to use mutable default value as an argument see: https://docs.quantifiedcode.com/python-anti-patterns/correctness/mutable_default_value_as_argument.html
	-- without timeout value in infinite loop
	-- use with to open files
        -- make sure this is run before sending patch:inspekt checkall --no-license-check <*>.py
