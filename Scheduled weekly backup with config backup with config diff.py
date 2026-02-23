# -*- coding: utf-8 -*-
#
# Aruba CX NAE Script - Weekly Scheduled & Config Change TFTP Backup
#
# This script performs automated backups of the switch running configuration
# to a TFTP server in two scenarios:
# 1. On a specific day of the week at a scheduled time (weekly backup)
# 2. Whenever a configuration change is detected (change-triggered backup)

"""
Combined Weekly Scheduled & Config Change TFTP Backup Agent

This NAE agent backs up the switch running configuration to a TFTP server:
- Weekly: On a specified day of the week at a specified time
- On-Change: Whenever a configuration change is detected

All backups are stored with timestamped filenames and include configuration
diff information in syslog for change-triggered backups.

Parameters:
    - tftp_server_address: IP address or hostname of the TFTP server
    - tftp_server_vrf: VRF to reach the TFTP server (default: mgmt)
    - tftp_configuration_format: Format for backup (cli or json, default: json)
    - file_name_prefix: Prefix for backup filename (timestamp will be appended)
    - backup_day_of_week: Day of week for weekly backup (Monday, Tuesday, etc.)
    - backup_time: Time of day for weekly backup in HH:MM:SS format
    - enable_weekly_backup: Enable/disable weekly scheduled backups (true/false)
    - enable_change_backup: Enable/disable config change backups (true/false)
"""

import time
from datetime import datetime
from re import sub

Manifest = {
    'Name': 'combined_tftp_backup',
    'Description': 'Backs up switch configuration to TFTP on weekly schedule and config changes',
    'Version': '1.7',
    'Author': 'Matthew Stegink',
    'AOSCXVersionMin': '10.08',
    'AOSCXPlatformList': ['8320', '8325', '8400', '6300', '6200', '6400']
}

ParameterDefinitions = {
    'tftp_server_address': {
        'Name': 'TFTP Server Address',
        'Description': 'IP address or hostname of the TFTP server',
        'Type': 'string',
        'Default': ''
    },
    'tftp_server_vrf': {
        'Name': 'VRF Name',
        'Description': 'VRF through which the TFTP server can be reached',
        'Type': 'string',
        'Default': 'mgmt'
    },
    'tftp_configuration_format': {
        'Name': 'Configuration Format',
        'Description': 'Format for configuration backup (cli or json)',
        'Type': 'string',
        'Default': 'json'
    },
    'file_name_prefix': {
        'Name': 'Filename Prefix',
        'Description': 'Prefix for backup filename (timestamp will be appended)',
        'Type': 'string',
        'Default': 'switch-backup-'
    },
    'backup_day_of_week': {
        'Name': 'Weekly Backup Day',
        'Description': 'Day of week for weekly backup (Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday)',
        'Type': 'string',
        'Default': 'Sunday'
    },
    'backup_time': {
        'Name': 'Weekly Backup Time',
        'Description': 'Time of day for weekly backup in HH:MM:SS format (24-hour)',
        'Type': 'string',
        'Default': '02:30:00'
    },
    'enable_weekly_backup': {
        'Name': 'Enable Weekly Backup',
        'Description': 'Enable or disable weekly scheduled backups (true or false)',
        'Type': 'string',
        'Default': 'true'
    },
    'enable_change_backup': {
        'Name': 'Enable Config Change Backup',
        'Description': 'Enable or disable backups on configuration changes (true or false)',
        'Type': 'string',
        'Default': 'true'
    }
}


class Agent(NAE):
    """
    NAE Agent for combined weekly scheduled and config change TFTP backups.
    
    This agent provides two backup mechanisms:
    1. Weekly backups on a scheduled day/time
    2. Automatic backups when configuration changes are detected
    
    Both can be independently enabled or disabled via parameters.
    """
    
    # Map day names to numbers (0=Monday, 6=Sunday)
    DAY_MAP = {
        'monday': 0,
        'tuesday': 1,
        'wednesday': 2,
        'thursday': 3,
        'friday': 4,
        'saturday': 5,
        'sunday': 6
    }
    
    def __init__(self):
        """Initialize the combined backup agent."""
        
        # Weekly scheduled backup rule - checks every 60 seconds
        self.schedule_rule = Rule('Weekly Backup Check')
        self.schedule_rule.condition('every 60 seconds')
        self.schedule_rule.action(self.check_weekly_backup_schedule)
        
        # Configuration change monitoring
        uri = '/rest/v1/system?attributes=last_configuration_time'
        rate_uri = Rate(uri, '10 seconds')
        self.monitor = Monitor(rate_uri, 'Rate of last configuration time')
        
        # Config change detection rule
        self.config_change_rule = Rule('Configuration change detection')
        self.config_change_rule.condition('{} > 0', [self.monitor])
        self.config_change_rule.action(self.store_base_checkpoint)
        self.config_change_rule.clear_condition('{} == 0', [self.monitor])
        self.config_change_rule.clear_action(self.handle_config_change)
        
        self.logger.info("Combined TFTP Backup Agent initialized")

    def check_weekly_backup_schedule(self, event):
        """
        Check if it's time to run the weekly backup.
        
        This method is called every 60 seconds. It checks if weekly backups
        are enabled, if today is the scheduled day, if the current time has 
        passed the scheduled backup time, and if a backup hasn't already been 
        performed this week.
        
        :param event: Event details passed by the NAE agent
        """
        try:
            # Check if weekly backup is enabled
            weekly_enabled = str(self.params['enable_weekly_backup']).strip().lower()
            if weekly_enabled != 'true':
                return
            
            current_datetime = datetime.now()
            current_time = current_datetime.strftime("%H:%M:%S")
            current_day = current_datetime.weekday()  # 0=Monday, 6=Sunday
            current_week = current_datetime.strftime('%Y-W%U')  # Year-Week number
            
            # Get and validate scheduled day
            scheduled_day_name = str(self.params['backup_day_of_week']).strip().lower()
            if scheduled_day_name not in self.DAY_MAP:
                self.logger.error(f"Invalid day: {scheduled_day_name}. Use Monday-Sunday")
                return
            
            scheduled_day = self.DAY_MAP[scheduled_day_name]
            
            # Get and validate scheduled time
            scheduled_time = str(self.params['backup_time']).strip()
            if not self._is_valid_time_format(scheduled_time):
                self.logger.error(f"Invalid time format: {scheduled_time}. Use HH:MM:SS")
                return
            
            # Check if backup already ran this week
            last_backup_week = self.variables.get('last_backup_week', '')
            
            # If today is the scheduled day, time has passed, and we haven't backed up this week
            if (current_day == scheduled_day and 
                current_time >= scheduled_time and 
                last_backup_week != current_week):
                
                day_name = scheduled_day_name.capitalize()
                self.logger.info(f"Weekly backup triggered on {day_name} at {current_time}")
                self.perform_backup('weekly_scheduled')
                self.variables['last_backup_week'] = current_week
                
        except Exception as e:
            self.logger.error(f"Error in check_weekly_backup_schedule: {e}")

    def store_base_checkpoint(self, event):
        """
        Store the base configuration checkpoint when a change is detected.
        
        This callback is triggered when configuration changes start. It stores
        the last checkpoint to use as a reference for calculating diffs.
        
        :param event: Event details passed by the NAE agent
        """
        try:
            uri = '/rest/configlist'
            configlist = self.get_rest_request_json(HTTP_ADDRESS + uri)
            if configlist:
                self.variables['base_checkpoint'] = configlist[-1]['name']
                self.logger.debug(f"Stored base checkpoint: {configlist[-1]['name']}")
        except Exception as e:
            self.logger.error(f"Could not get checkpoint list: {e}")

    def handle_config_change(self, event):
        """
        Handle configuration change completion.
        
        This callback is triggered when configuration changes are complete 
        (rate returns to zero). It logs the change, shows diffs, and triggers
        a backup if config change backups are enabled.
        
        :param event: Event details passed by the NAE agent
        """
        try:
            # Check if config change backup is enabled
            change_enabled = str(self.params['enable_change_backup']).strip().lower()
            if change_enabled != 'true':
                return
            
            # Get base checkpoint for diff
            if 'base_checkpoint' in self.variables:
                base_checkpoint = self.variables['base_checkpoint']
            else:
                base_checkpoint = 'startup-config'
            
            # Log the configuration change
            ActionSyslog('Configuration change detected - backup initiated')
            ActionCLI('show system', title=Title("System information"))
            
            # Show configuration differences
            ActionCLI(f'checkpoint diff {base_checkpoint} running-config',
                      title=Title("Configuration changes since latest checkpoint"))
            
            if base_checkpoint != 'startup-config':
                ActionCLI('checkpoint diff startup-config running-config',
                          title=Title("Unsaved configuration changes"))
            
            # Show audit logs
            ActionShell('ausearch -i -m USYS_CONFIG -ts recent',
                        title=Title("Recent audit logs for configuration changes"))
            
            # Perform backup
            self.logger.info("Configuration change backup triggered")
            self.perform_backup('config_change')
            
        except Exception as e:
            self.logger.error(f"Error in handle_config_change: {e}")

    def perform_backup(self, backup_type):
        """
        Perform the actual TFTP backup operation.
        
        Validates parameters and executes the TFTP copy command to back up
        the running configuration.
        
        :param backup_type: Type of backup triggering this action 
                           ('weekly_scheduled' or 'config_change')
        """
        try:
            # Validate TFTP server address
            tftp_server = str(self.params['tftp_server_address']).strip()
            if not tftp_server:
                self.logger.error("TFTP server address not configured")
                ActionSyslog(f"{backup_type} backup failed: TFTP server not configured", 
                           severity='WARNING')
                return
            
            # Validate filename prefix
            file_prefix = str(self.params['file_name_prefix']).strip()
            if not file_prefix:
                self.logger.error("Filename prefix not configured")
                ActionSyslog(f"{backup_type} backup failed: Filename prefix not configured",
                           severity='WARNING')
                return
            
            # Get and validate format
            config_format = str(self.params['tftp_configuration_format']).strip().lower()
            if config_format not in ('json', 'cli'):
                self.logger.warning(f"Invalid format '{config_format}', using 'json'")
                config_format = 'json'
            
            # Get VRF
            vrf = str(self.params['tftp_server_vrf']).strip()
            
            # Execute backup
            self.logger.info(f"Starting {backup_type} backup to TFTP server {tftp_server}")
            self._tftp_copy(tftp_server, file_prefix, vrf, config_format, backup_type)
            
            ActionSyslog(f"{backup_type} configuration backup completed to {tftp_server}",
                       severity='INFO')
            
        except Exception as e:
            self.logger.error(f"Backup failed: {e}")
            ActionSyslog(f"{backup_type} backup failed: {e}", severity='ERR')

    def _tftp_copy(self, tftp_server, file_prefix, vrf, config_format, backup_type):
        """
        Execute the TFTP copy command.
        
        :param tftp_server: IP address or hostname of TFTP server
        :param file_prefix: Prefix for the backup filename
        :param vrf: VRF name to reach the TFTP server
        :param config_format: Format for backup (json or cli)
        :param backup_type: Type of backup for filename tagging
        """
        # Generate timestamped filename with backup type
        timestamp = int(time.time())
        file_extension = '.json' if config_format == 'json' else '.cfg'
        filename = f"{file_prefix}{backup_type}-{timestamp}{file_extension}"
        
        # Build TFTP command
        tftp_command = f'copy running-config tftp://{tftp_server}/{filename} {config_format}'
        
        if vrf:
            tftp_command += f' vrf {vrf}'
        
        self.logger.info(f"Executing: {tftp_command}")
        ActionCLI(tftp_command)

    def _is_valid_time_format(self, time_str):
        """
        Validate time string is in HH:MM:SS format.
        
        :param time_str: Time string to validate
        :return: True if valid, False otherwise
        """
        try:
            parts = time_str.split(':')
            if len(parts) != 3:
                return False
            
            hour, minute, second = map(int, parts)
            
            if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
                return False
            
            return True
        except (ValueError, AttributeError):
            return False