# MCP Tool Inventory

Complete, read-only inventory of every MCP tool exposed by this repository's active MCP servers, generated from direct source inspection (OpenAPI spec for auto-generated tools; `@mcp.tool` declarations and their implementations for hand-registered tools). CMS is included because it is still currently exposed, despite being planned for retirement.

**Capability** — READ: retrieves information without mutating meaningful system state. CONTROL: changes operational/runtime state but is generally reversible. WRITE: creates/updates business/data state. DESTRUCTIVE: deletion, irreversible action, or a potentially dangerous system/hardware action.

**Risk** — LOW / MEDIUM / HIGH / CRITICAL, based on actual implementation (backend call, blast radius, reversibility), not the tool's name.

**Approval required** — whether a human should confirm before an agent invokes this tool autonomously. This reflects a *recommendation*, not necessarily an already-implemented technical gate; where a real two-step confirmation-token gate exists in code (`charger_ssh_prepare`/`execute`, `cms_run_device_action_prepare`/`execute`), that is noted in the tool's purpose.

## Summary counts

- **Total tools: 233**
- READ: 209
- CONTROL: 6
- WRITE: 17
- DESTRUCTIVE: 1
- Approval required (YES): 3

Risk breakdown: LOW 177, MEDIUM 54, HIGH 1, CRITICAL 1.

By server: noc 184, cms 17, jira 5, chargezone 8, ecog-sd 5, kb 3, docgen 3, platform 8.

## The five newly added tools

| Tool | Server | Capability | Risk | Approval | Why |
|---|---|---|---|---|---|
| `jira_project_meta` | jira | READ | LOW | NO | Reference metadata for an Ecog Jira project: statuses, issue types, priorities |
| `kb_list_scopes` | kb | READ | LOW | NO | List configured knowledge scopes and search profiles from scopes.yaml (configuration shape only) |
| `kb_ingest_status` | kb | READ | LOW | NO | Report live index state (documents indexed per collection); never triggers ingestion |
| `platform_mcp_discovery` | platform | READ | LOW | NO | Static source scan of services/mcp/* for @mcp.tool declarations across all servers |
| `platform_config_validate` | platform | READ | LOW | NO | Cross-check docker-compose.yml / librechat.yaml / services/mcp/* for configuration drift |

## NOC (`services/mcp/noc/`) — 184 tools

| Tool | Domain | Purpose | Capability | Risk | Approval |
|---|---|---|---|---|---|
| `me_api_v1_auth_me_get` | Auth / RBAC | Me | READ | MEDIUM | NO |
| `list_users_api_v1_auth_users_get` | Auth / RBAC | List Users | READ | MEDIUM | NO |
| `get_user_audit_logs_api_v1_auth_users__user_id__audit_logs_get` | Auth / RBAC | Get User Audit Logs | READ | MEDIUM | NO |
| `get_user_extra_permissions_endpoint_api_v1_auth_users__user_id__extra_permissions_get` | Auth / RBAC | Get User Extra Permissions Endpoint | READ | MEDIUM | NO |
| `get_user_denied_permissions_endpoint_api_v1_auth_users__user_id__denied_permissions_get` | Auth / RBAC | Get User Denied Permissions Endpoint | READ | MEDIUM | NO |
| `get_user_effective_permissions_api_v1_auth_users__user_id__effective_permissions_get` | Auth / RBAC | Get User Effective Permissions | READ | MEDIUM | NO |
| `get_my_effective_notification_preferences_api_v1_auth_me_notification_preferences_get` | Auth / RBAC | Get My Effective Notification Preferences | READ | MEDIUM | NO |
| `get_user_notification_preferences_admin_api_v1_auth_users__user_id__notification_preferences_get` | Auth / RBAC | Get User Notification Preferences Admin | READ | MEDIUM | NO |
| `get_live_session_api_v1_public_chargers__charger_id__session_live_get` | Public (customer-facing) | Get live or latest session for a charger (public) | READ | MEDIUM | NO |
| `get_charger_by_serial_api_v1_public_chargers_by_serial_get` | Public (customer-facing) | Get charger by serial number (public) | READ | MEDIUM | NO |
| `generate_public_qr_token_api_v1_public_chargers__charger_id__qr_token_get` | Public (customer-facing) | Generate signed QR token for a charger (public) | CONTROL | MEDIUM | NO |
| `get_charger_visits_public_api_v1_public_chargers__charger_id__visits_get` | Public (customer-facing) | List active visits for a charger (public) | READ | MEDIUM | NO |
| `get_public_release_notes_api_v1_public_releases__release_id__notes_get` | Public (customer-facing) | Get Public Release Notes | READ | MEDIUM | NO |
| `list_chargers_api_v1_chargers_get` | Fleet & Chargers | List all chargers | READ | LOW | NO |
| `dashboard_summary_api_v1_dashboard_summary_get` | Fleet & Chargers | Dashboard summary | READ | LOW | NO |
| `get_chargers_for_map_api_v1_chargers_map_get` | Fleet & Chargers | Get all chargers for map view | READ | LOW | NO |
| `get_charger_by_id_api_v1_chargers__charger_id__get` | Fleet & Chargers | Get a single charger | READ | LOW | NO |
| `list_charger_models_api_v1_charger_models_get` | Fleet & Chargers | List all charger models | READ | LOW | NO |
| `get_charger_model_api_v1_charger_models__model_id__get` | Fleet & Chargers | Get a charger model | READ | LOW | NO |
| `list_customers_api_v1_customers_get` | Fleet & Chargers | List all customers | READ | LOW | NO |
| `get_customer_api_v1_customers__customer_id__get` | Fleet & Chargers | Get a customer | READ | LOW | NO |
| `get_latest_telemetry_api_v1_chargers__charger_id__telemetry_latest_get` | Telemetry & Sessions | Latest telemetry snapshot | READ | LOW | NO |
| `get_telemetry_history_api_v1_chargers__charger_id__telemetry_history_get` | Telemetry & Sessions | Paginated telemetry history | READ | LOW | NO |
| `get_alarm_history_api_v1_chargers__charger_id__history_alarms_get` | Fleet & Chargers | Paginated alarm history | READ | LOW | NO |
| `get_active_sessions_api_v1_chargers__charger_id__sessions_active_get` | Telemetry & Sessions | Get currently active charging sessions | READ | LOW | NO |
| `get_session_history_api_v1_chargers__charger_id__history_sessions_get` | Telemetry & Sessions | Paginated charging session history | READ | LOW | NO |
| `get_session_by_id_endpoint_api_v1_sessions__session_id__get` | Telemetry & Sessions | Get a single session by ID | READ | LOW | NO |
| `get_session_events_api_v1_chargers__charger_id__sessions__session_id__events_get` | Telemetry & Sessions | Get session lifecycle events | READ | LOW | NO |
| `get_state_logs_api_v1_chargers__charger_id__state_logs_get` | Telemetry & Sessions | State logs from GenOne telemetry | READ | LOW | NO |
| `get_state_log_api_v1_chargers__charger_id__state_logs__log_id__get` | Telemetry & Sessions | Get a single state log | READ | LOW | NO |
| `get_telemetry_aggregated_api_v1_chargers__charger_id__telemetry_aggregated_get` | Telemetry & Sessions | Aggregated telemetry from TimescaleDB | READ | LOW | NO |
| `get_state_log_export_job_api_v1_exports__job_id__get` | Fleet & Chargers | Get state-log export job status | READ | LOW | NO |
| `get_commands_api_v1_chargers__charger_id__commands_get` | Telemetry & Sessions | Command history for a charger | READ | LOW | NO |
| `get_command_api_v1_commands__command_id__get` | Telemetry & Sessions | Get a single command with its result | READ | LOW | NO |
| `get_stats_api_v1_stats_get` | Fleet & Chargers | System-wide statistics | READ | LOW | NO |
| `get_session_statistics_api_v1_stats_sessions_get` | Telemetry & Sessions | Fleet-wide session statistics | READ | LOW | NO |
| `get_failed_sessions_api_v1_stats_sessions_failed_get` | Telemetry & Sessions | Detailed failed session analysis | READ | LOW | NO |
| `get_filtered_sessions_api_v1_stats_sessions_list_get` | Telemetry & Sessions | Filtered session history | READ | LOW | NO |
| `get_alarm_statistics_api_v1_stats_alarms_get` | Alarms & Monitoring | Fleet-wide alarm statistics | READ | LOW | NO |
| `get_filtered_alarms_api_v1_stats_alarms_list_get` | Alarms & Monitoring | Filtered alarm history | READ | LOW | NO |
| `error_trends_api_v1_stats_error_trends_get` | Fleet & Chargers | Error trend time series | READ | LOW | NO |
| `error_trends_by_client_api_v1_stats_error_trends_by_client_get` | Fleet & Chargers | Customer error ranking | READ | LOW | NO |
| `error_trends_by_error_type_api_v1_stats_error_trends_by_error_type_get` | Fleet & Chargers | Error type breakdown | READ | LOW | NO |
| `error_trends_summary_api_v1_stats_error_trends_summary_get` | Fleet & Chargers | Error trends summary cards | READ | LOW | NO |
| `error_trends_export_api_v1_stats_error_trends_export_get` | Fleet & Chargers | Export error trends data | READ | LOW | NO |
| `get_charger_versions_api_v1_chargers__charger_id__versions_get` | Fleet & Chargers | Get software versions for a charger | READ | LOW | NO |
| `get_charger_stability_api_v1_chargers__charger_id__stability_get` | Fleet & Chargers | Get connection stability for a charger | READ | LOW | NO |
| `get_ocpp_config_api_v1_chargers__charger_id__config_ocpp_get` | Fleet & Chargers | Get OCPP configuration from charger | READ | LOW | NO |
| `get_ocpp_config_cache_api_v1_chargers__charger_id__ocpp_config_cache_get` | Fleet & Chargers | Read cached OCPP configuration for a charger | READ | LOW | NO |
| `upload_status_api_v1_chargers__charger_id__upload_status__upload_id__get` | Fleet & Chargers | Check chunked upload processing status | READ | LOW | NO |
| `get_ssh_status_api_v1_chargers__charger_id__ssh_status_get` | SSH & Proxy | Get SSH connection status | READ | MEDIUM | NO |
| `get_proxy_status_api_v1_ssh_status_get` | SSH & Proxy | Get overall SSH proxy status | READ | MEDIUM | NO |
| `list_categories_api_v1_ticket_categories_get` | Tickets & Support | List ticket categories | READ | LOW | NO |
| `list_tickets_api_v1_tickets_get` | Tickets & Support | List tickets | READ | LOW | NO |
| `export_tickets_api_v1_tickets_export_get` | Tickets & Support | Export tickets to CSV | READ | LOW | NO |
| `get_ticket_api_v1_tickets__ticket_id__get` | Tickets & Support | Get ticket detail | READ | LOW | NO |
| `list_comments_api_v1_tickets__ticket_id__comments_get` | Tickets & Support | List comments | READ | LOW | NO |
| `list_attachments_api_v1_tickets__ticket_id__attachments_get` | Tickets & Support | List attachments | READ | LOW | NO |
| `download_attachment_api_v1_ticket_attachments__attachment_id__download_get` | Tickets & Support | Download attachment via S3 presigned URL | READ | LOW | NO |
| `get_history_api_v1_tickets__ticket_id__history_get` | Tickets & Support | Get ticket audit history | READ | LOW | NO |
| `list_visits_api_v1_tickets__ticket_id__visits_get` | Tickets & Support | List visit logs | READ | LOW | NO |
| `dashboard_stats_api_v1_tickets_stats_dashboard_get` | Tickets & Support | Ticket dashboard stats | READ | LOW | NO |
| `engineer_stats_api_v1_tickets_stats_engineers_get` | Tickets & Support | Engineer performance | READ | LOW | NO |
| `charger_fault_stats_api_v1_tickets_stats_chargers_get` | Tickets & Support | Repeated charger faults | READ | LOW | NO |
| `list_tags_api_v1_ticket_tags_get` | Tickets & Support | List all tags | READ | LOW | NO |
| `list_auto_rules_api_v1_ticket_auto_rules_get` | Tickets & Support | List auto-ticket rules | READ | LOW | NO |
| `list_notifications_api_v1_ticket_notifications_get` | Tickets & Support | List in-app notifications | READ | LOW | NO |
| `notification_count_api_v1_ticket_notifications_count_get` | Tickets & Support | Get notification count | READ | LOW | NO |
| `unread_count_api_v1_ticket_notifications_unread_count_get` | Tickets & Support | Get unread notification count | READ | LOW | NO |
| `get_ticket_settings_api_v1_ticket_settings_get` | Tickets & Support | Get ticket settings | READ | LOW | NO |
| `list_vendor_codes_api_v1_ticket_settings_vendor_codes_get` | Tickets & Support | List all vendor error codes | READ | LOW | NO |
| `get_sla_rules_api_v1_ticket_settings_sla_rules_get` | Tickets & Support | List SLA rules | READ | LOW | NO |
| `get_smtp_settings_endpoint_api_v1_smtp_settings_get` | Admin & Config | Get Smtp Settings Endpoint | READ | MEDIUM | NO |
| `get_email_notification_settings_endpoint_api_v1_email_notification_settings_get` | Admin & Config | Get Email Notification Settings Endpoint | READ | MEDIUM | NO |
| `list_roles_api_v1_roles_get` | Auth / RBAC | List Roles | READ | MEDIUM | NO |
| `get_role_api_v1_roles__slug__get` | Auth / RBAC | Get Role | READ | MEDIUM | NO |
| `list_role_permissions_api_v1_roles__slug__permissions_get` | Auth / RBAC | List Role Permissions | READ | MEDIUM | NO |
| `get_role_permission_preview_api_v1_roles__slug__permission_preview_get` | Auth / RBAC | Get Role Permission Preview | READ | MEDIUM | NO |
| `list_teams_api_v1_teams_get` | Auth / RBAC | List Teams | READ | MEDIUM | NO |
| `get_team_api_v1_teams__slug__get` | Auth / RBAC | Get Team | READ | MEDIUM | NO |
| `list_team_members_api_v1_teams__slug__members_get` | Auth / RBAC | List Team Members | READ | MEDIUM | NO |
| `users_for_assignment_api_v1_teams__slug__users_for_assignment_get` | Auth / RBAC | Users For Assignment | READ | MEDIUM | NO |
| `get_team_permissions_api_v1_teams__slug__permissions_get` | Auth / RBAC | Get Team Permissions | READ | MEDIUM | NO |
| `list_permissions_api_v1_permissions_get` | Auth / RBAC | List Permissions | READ | MEDIUM | NO |
| `list_categories_api_v1_permissions_categories_get` | Auth / RBAC | List Categories | READ | MEDIUM | NO |
| `get_dashboard_summary_api_v1_monitoring_dashboard_summary_get` | Alarms & Monitoring | Monitoring dashboard summary | READ | LOW | NO |
| `get_services_health_api_v1_monitoring_services_health_get` | Alarms & Monitoring | Real-time service health checks | READ | LOW | NO |
| `get_aws_infrastructure_api_v1_monitoring_infrastructure_aws_get` | Alarms & Monitoring | AWS resource status and metrics | READ | MEDIUM | NO |
| `query_cloudwatch_metric_api_v1_monitoring_infrastructure_aws_metrics_get` | Alarms & Monitoring | Generic CloudWatch metric query | READ | MEDIUM | NO |
| `query_metrics_api_v1_monitoring_metrics_get` | Alarms & Monitoring | Query stored application/system metrics | READ | LOW | NO |
| `list_available_metrics_api_v1_monitoring_metrics_available_get` | Alarms & Monitoring | List available metrics and dimensions | READ | LOW | NO |
| `get_application_performance_api_v1_monitoring_application_performance_get` | Alarms & Monitoring | Application performance summary | READ | LOW | NO |
| `get_container_metrics_api_v1_monitoring_containers_get` | Alarms & Monitoring | Container CPU and memory metrics | READ | LOW | NO |
| `get_container_snapshot_api_v1_monitoring_containers_snapshot_get` | Alarms & Monitoring | Real-time container stats snapshot | READ | LOW | NO |
| `get_charger_communication_health_api_v1_monitoring_charger_communication_get` | Alarms & Monitoring | Charger communication health | READ | LOW | NO |
| `list_alerts_api_v1_monitoring_alerts_get` | Alarms & Monitoring | List alert history | READ | LOW | NO |
| `list_alert_rules_api_v1_monitoring_alert_rules_get` | Alarms & Monitoring | List alert rules | READ | LOW | NO |
| `list_dashboards_api_v1_monitoring_dashboards_get` | Alarms & Monitoring | List dashboard configs | READ | LOW | NO |
| `list_maintenance_windows_api_v1_monitoring_maintenance_windows_get` | Alarms & Monitoring | List maintenance windows | READ | LOW | NO |
| `list_services_api_v1_logs_services_get` | Logs | List Services | READ | LOW | NO |
| `get_logs_api_v1_logs_get` | Logs | Get Logs | READ | LOW | NO |
| `get_log_download_status_api_v1_chargers__charger_id__logs_download__job_id__get` | Fleet & Chargers | Get Log Download Status | READ | LOW | NO |
| `redirect_to_log_download_api_v1_chargers__charger_id__logs_download__job_id__download_get` | Fleet & Chargers | Redirect To Log Download | READ | LOW | NO |
| `list_log_downloads_api_v1_chargers__charger_id__logs_downloads_get` | Fleet & Chargers | List Log Downloads | READ | LOW | NO |
| `list_releases_api_v1_releases_get` | Releases & Engineering | List Releases | READ | LOW | NO |
| `get_stats_api_v1_releases_stats_get` | Releases & Engineering | Get Stats | READ | LOW | NO |
| `get_release_notes_draft_api_v1_releases__release_id__notes_draft_get` | Releases & Engineering | Get Release Notes Draft | READ | LOW | NO |
| `get_release_api_v1_releases__release_id__get` | Releases & Engineering | Get Release | READ | LOW | NO |
| `list_tasks_by_release_api_v1_releases__release_id__tasks_get` | Releases & Engineering | List Tasks By Release | READ | LOW | NO |
| `get_task_api_v1_releases__release_id__tasks__task_id__get` | Releases & Engineering | Get Task | READ | LOW | NO |
| `list_bugs_by_release_api_v1_releases__release_id__bugs_get` | Releases & Engineering | List Bugs By Release | READ | LOW | NO |
| `get_bug_api_v1_releases__release_id__bugs__bug_id__get` | Releases & Engineering | Get Bug | READ | LOW | NO |
| `list_comments_api_v1_releases__release_id__comments_get` | Releases & Engineering | List Comments | READ | LOW | NO |
| `list_bug_comments_api_v1_releases__release_id__bugs__bug_id__comments_get` | Releases & Engineering | List Bug Comments | READ | LOW | NO |
| `list_bug_timeline_api_v1_releases__release_id__bugs__bug_id__timeline_get` | Releases & Engineering | List Bug Timeline | READ | LOW | NO |
| `list_timeline_api_v1_releases__release_id__timeline_get` | Releases & Engineering | List Timeline | READ | LOW | NO |
| `get_upload_status_api_v1_admin_rule_files_status_get` | Admin & Config | Get Upload Status | READ | MEDIUM | NO |
| `get_endpoints_catalog_api_v1_admin_endpoints_catalog_get` | Admin & Config | Get Endpoints Catalog | READ | MEDIUM | NO |
| `get_alarm_mappings_api_v1_admin_alarm_mappings_get` | Admin & Config | Get Alarm Mappings | READ | MEDIUM | NO |
| `list_rbac_rules_api_v1_admin_rules_rbac_get` | Admin & Config | List Rbac Rules | READ | MEDIUM | NO |
| `list_alarm_mappings_api_v1_admin_rules_alarm_mappings_get` | Admin & Config | List Alarm Mappings | READ | MEDIUM | NO |
| `list_genone_api_api_v1_admin_rules_genone_api_get` | Admin & Config | List Genone Api | READ | MEDIUM | NO |
| `get_ecog_action_apis_api_v1_admin_ecog_action_apis_get` | Admin & Config | List configured ECOG charger action APIs | READ | MEDIUM | NO |
| `get_ecog_action_api_api_v1_admin_ecog_action_apis__api_id__get` | Admin & Config | Get a single ECOG action API definition | READ | MEDIUM | NO |
| `lookup_coordinates_api_v1_geo_lookup_get` | Map / Geo | Find state, region, and country by coordinates | READ | LOW | NO |
| `get_map_filters_api_v1_map_filters_get` | Map / Geo | Get map filter options | READ | LOW | NO |
| `list_nightly_restart_jobs_api_v1_nightly_restart_jobs_get` | Admin & Config | List nightly charger-restart batch runs | READ | MEDIUM | NO |
| `get_nightly_restart_job_api_v1_nightly_restart_jobs__job_id__get` | Admin & Config | Get one nightly restart batch with per-charger results | READ | MEDIUM | NO |
| `get_nightly_restart_settings_endpoint_api_v1_nightly_restart_settings_get` | Admin & Config | Get nightly restart window settings | READ | MEDIUM | NO |
| `report_session_details_api_v1_reports_session_details_get` | Reporting | Bulk session details report | READ | LOW | NO |
| `report_customer_status_api_v1_reports_customer_status_get` | Reporting | Customer-wise charger status report | READ | LOW | NO |
| `report_commissioned_api_v1_reports_commissioned_get` | Reporting | Commissioned chargers report | READ | LOW | NO |
| `report_commission_pending_api_v1_reports_commission_pending_get` | Reporting | Commission-pending chargers report | READ | LOW | NO |
| `report_alert_details_api_v1_reports_alert_details_get` | Alarms & Monitoring | Alert / alarm details report | READ | LOW | NO |
| `report_charger_ticket_status_api_v1_reports_charger_ticket_status_get` | Reporting | Charger status with open ticket counts | READ | LOW | NO |
| `report_warranty_summary_api_v1_reports_warranty_summary_get` | Reporting | Charger warranty summary report | READ | LOW | NO |
| `report_user_permissions_api_v1_reports_user_permissions_get` | Reporting | User roles and permissions report | READ | MEDIUM | NO |
| `report_fault_frequency_api_v1_reports_fault_frequency_get` | Reporting | Fault frequency analysis by alarm key and error code | READ | LOW | NO |
| `report_session_failures_api_v1_reports_session_failures_get` | Reporting | Sessions that ended with abnormal stop reasons | READ | LOW | NO |
| `report_nightly_restart_api_v1_reports_nightly_restart_get` | Reporting | Nightly restart job summary report | READ | LOW | NO |
| `report_ticket_resolution_api_v1_reports_ticket_resolution_get` | Reporting | Ticket lifecycle and resolution time report | READ | LOW | NO |
| `report_charger_energy_api_v1_reports_charger_energy_get` | Reporting | Per-charger session count and energy delivery summary | READ | LOW | NO |
| `list_vendors_api_v1_vendors_get` | Vendors & CPOs | List all vendors | READ | LOW | NO |
| `get_vendor_api_v1_vendors__vendor_id__get` | Vendors & CPOs | Get a vendor | READ | LOW | NO |
| `list_cpos_api_v1_cpos_get` | Vendors & CPOs | List all CPOs | READ | LOW | NO |
| `get_cpo_api_v1_cpos__cpo_id__get` | Vendors & CPOs | Get a CPO | READ | LOW | NO |
| `list_error_code_configs_api_v1_error_code_configs_get` | Admin & Config | List all error code configs | READ | MEDIUM | NO |
| `get_error_code_config_api_v1_error_code_configs__config_id__get` | Admin & Config | Get an error code config | READ | MEDIUM | NO |
| `health_check_health_get` | Health | Health Check | READ | LOW | NO |
| `proxy_api_call_path_based_api_v1_chargers__charger_id__proxy__port___path__get` | SSH & Proxy | Proxy API call to charger (path-based, for HMI) | READ | MEDIUM | NO |
| `proxy_api_call_api_v1_chargers__charger_id__proxy_get` | SSH & Proxy | Proxy API call to charger (query-based, for web tools) | READ | MEDIUM | NO |
| `is_live_api_charger_IsLive_get` | Health | Is Live | READ | LOW | NO |
| `log_version_api_charger_LogVersion_get` | Health | Log Version | READ | LOW | NO |
| `get_control_center_api_catalog` | On-Charger Control Center | RBAC-authoritative catalog of ~124 GET endpoints across on-charger services (network/RFID/power/charging/dev-tools) | READ | LOW | NO |
| `charger_get_charging_status` | On-Charger Control Center | Per-gun connected/PLC/OCPP live charging status | READ | LOW | NO |
| `charger_get_session_details` | On-Charger Control Center | Active/last session or recent session history for a charger | READ | LOW | NO |
| `charger_get_meters` | On-Charger Control Center | Live AC/DC meter readings (+ IMD when gun_id given) | READ | LOW | NO |
| `charger_get_ocpp_status` | On-Charger Control Center | OCPP connection status and config keys | READ | LOW | NO |
| `charger_get_network_status` | On-Charger Control Center | Network + GSM signal/operator status | READ | LOW | NO |
| `charger_get_system_info` | On-Charger Control Center | Hardware, software version, and RTC identity | READ | LOW | NO |
| `charger_get_allocation_state` | On-Charger Control Center | Per-gun power allocation state | READ | LOW | NO |
| `charger_get_pm_power` | On-Charger Control Center | Power-module count and per-module output | READ | LOW | NO |
| `charger_get_alarms` | On-Charger Control Center | Latched alarms and pending errors (fleet or per-gun) | READ | LOW | NO |
| `charger_get_temperature` | On-Charger Control Center | Thermal sensor readings and thresholds | READ | LOW | NO |
| `charger_list_log_sources` | On-Charger Control Center | Enumerate on-charger log sources (admin-tier account) | READ | MEDIUM | NO |
| `charger_get_logs` | On-Charger Control Center | Fetch logs for one on-charger source (admin-tier account) | READ | MEDIUM | NO |
| `charger_get_device_metrics` | On-Charger Control Center | On-charger CPU/memory/disk metrics (admin-tier account) | READ | MEDIUM | NO |
| `ticket_create` | Tickets & Support | Create a new ticket, optionally linked to a charger/session/alarm | WRITE | LOW | NO |
| `ticket_add_comment` | Tickets & Support | Add an internal or customer-visible comment to a ticket | WRITE | LOW | NO |
| `ticket_assign` | Tickets & Support | Assign, reassign, or unassign a ticket to a user and/or team | WRITE | LOW | NO |
| `ticket_set_status` | Tickets & Support | Change a ticket's status (Open/Assigned/.../Closed/Reopened) | WRITE | LOW | NO |
| `ticket_add_visit` | Tickets & Support | Log a field-service visit (findings/actions/next steps) | WRITE | LOW | NO |
| `ticket_reopen` | Tickets & Support | Reopen a closed ticket with a required reason | WRITE | LOW | NO |
| `ticket_verify` | Tickets & Support | Verify or reject a ticket's resolution | WRITE | MEDIUM | NO |
| `ticket_update` | Tickets & Support | Partially update ticket fields (title, priority, assignment, etc.) | WRITE | LOW | NO |
| `ticket_link` | Tickets & Support | Link two tickets (e.g. mark as duplicate) | WRITE | LOW | NO |
| `alert_acknowledge` | Alarms & Monitoring | Mark an alert as being looked at (no resolution implied) | WRITE | LOW | NO |
| `alert_resolve` | Alarms & Monitoring | Mark an alert resolved once the underlying issue is actually fixed | WRITE | MEDIUM | NO |
| `charger_request_log_download` | Logs | Start an async on-charger log-download job (returns a job id, not log content) | WRITE | LOW | NO |
| `charger_export_state_logs` | Logs | Request a state-log export/zip job by log id or time range | WRITE | LOW | NO |
| `maintenance_window_create` | Admin & Config | Create a maintenance window that suppresses alerts/notifications fleet-wide for a time range | CONTROL | MEDIUM | YES |
| `charger_generate_qr_token` | Charger Support | Mint a signed QR token for the customer-facing QR-scan ticket flow | WRITE | LOW | NO |
| `charger_ssh_prepare` | SSH & Proxy | Stage an arbitrary shell command for a charger and return a one-time confirmation token (does not execute) | CONTROL | LOW | NO |
| `charger_ssh_execute` | SSH & Proxy | Execute the staged command on a live charger's SSH PTY using the one-time token (no command denylist) | DESTRUCTIVE | CRITICAL | YES |

## CMS (`services/mcp/cms/`) — 17 tools — planned for retirement

| Tool | Domain | Purpose | Capability | Risk | Approval |
|---|---|---|---|---|---|
| `cms_get_charger_details` | Fleet & Chargers | Full profile for one charger: metadata + live status merged | READ | LOW | NO |
| `cms_get_charger_sessions` | Telemetry & Sessions | Recent charging-session logs for one charger | READ | LOW | NO |
| `cms_get_charger_alerts` | Alarms & Monitoring | Recent alert/fault logs for one charger | READ | LOW | NO |
| `cms_get_charger_action_history` | Device Commands | Remote-command execution history for one charger (does not run any command) | READ | LOW | NO |
| `cms_list_charger_actions` | Device Commands | Catalog of device API commands available for a charger (reference only) | READ | LOW | NO |
| `cms_list_chargers` | Fleet & Chargers | List all chargers (id + customer), searchable | READ | LOW | NO |
| `cms_get_charger_types` | Fleet & Chargers | Charger types/models defined in the legacy CMS | READ | LOW | NO |
| `cms_get_noc_summary` | Fleet & Chargers | Fleet totals per customer: total/online/offline/faulty | READ | LOW | NO |
| `cms_get_recent_alerts` | Alarms & Monitoring | Fleet-wide recent alerts/faults | READ | LOW | NO |
| `cms_list_customers` | Customers & Engineers | Customers in the legacy CMS (id, name, contact, country) | READ | LOW | NO |
| `cms_list_service_engineers` | Customers & Engineers | Service engineers registered in the legacy CMS | READ | LOW | NO |
| `cms_get_charger_firmware` | Fleet & Chargers | Firmware/release version running on one charger | READ | LOW | NO |
| `cms_get_fleet_firmware` | Fleet & Chargers | Firmware/release versions across many chargers (bounded fan-out, online-only by default) | READ | LOW | NO |
| `cms_run_device_query` | Device Commands | Run a read-only device diagnostic (GET-type command only) on one live charger | READ | MEDIUM | NO |
| `cms_run_device_action_prepare` | Device Commands | Stage a write-type device command from an 8-command allowlist; returns a one-time confirmation token (does not execute) | CONTROL | MEDIUM | NO |
| `cms_run_device_action_execute` | Device Commands | Execute an allowlisted, token-approved write command on a live charger (reset, reboot, stop outlet, OCPP restart/start/stop, clear RFID cache) | CONTROL | HIGH | YES |
| `cms_read_device_log` | Device Commands | Read a session/alert state-log or command-output blob by server file path (path regex-constrained) | READ | LOW | NO |

## Jira (`services/mcp/jira/`) — 5 tools

| Tool | Domain | Purpose | Capability | Risk | Approval |
|---|---|---|---|---|---|
| `jira_search` | Issue Tracking | Search Ecog Jira issues with a JQL query | READ | LOW | NO |
| `jira_get_issue` | Issue Tracking | Full detail of one Ecog Jira issue by key | READ | LOW | NO |
| `jira_get_comments` | Issue Tracking | Comments on an Ecog Jira issue | READ | LOW | NO |
| `jira_get_issue_transitions` | Issue Tracking | Current status plus available workflow transitions (reads options only, never transitions) | READ | LOW | NO |
| `jira_project_meta` 🆕 | Issue Tracking | Reference metadata for an Ecog Jira project: statuses, issue types, priorities | READ | LOW | NO |

## ChargeZone Jira (`services/mcp/chargezone/`) — 8 tools

| Tool | Domain | Purpose | Capability | Risk | Approval |
|---|---|---|---|---|---|
| `cz_jira_search` | Issue Tracking | Search Quench (QCH) Jira issues, hard-scoped to the QCH project | READ | LOW | NO |
| `cz_jira_get_issue` | Issue Tracking | Full detail of one QCH Jira issue by key | READ | LOW | NO |
| `cz_jira_get_comments` | Issue Tracking | Comments on a QCH Jira issue | READ | LOW | NO |
| `cz_jira_get_issue_transitions` | Issue Tracking | Current status plus available workflow transitions for a QCH issue | READ | LOW | NO |
| `cz_jira_export` | Issue Tracking | Bulk export of QCH issues matching a filter | READ | LOW | NO |
| `cz_jira_stats` | Reporting | Aggregate QCH ticket statistics (by status/type/priority/assignee/trend) | READ | LOW | NO |
| `cz_jira_project_meta` | Issue Tracking | Reference metadata for the QCH project: statuses, issue types, priorities | READ | LOW | NO |
| `cz_jira_list_dashboards` | Reporting | List Jira dashboards and saved filters referencing QCH | READ | LOW | NO |

## Ecog Service Desk (`services/mcp/ecog-sd/`) — 5 tools

| Tool | Domain | Purpose | Capability | Risk | Approval |
|---|---|---|---|---|---|
| `sd_list_requests` | Service Desk | List Ecog Service Desk customer requests, filterable by status/ownership | READ | LOW | NO |
| `sd_get_request` | Service Desk | Full detail of one Service Desk request | READ | LOW | NO |
| `sd_get_request_comments` | Service Desk | Comments on a Service Desk request | READ | LOW | NO |
| `sd_list_servicedesks` | Service Desk | List available service desks | READ | LOW | NO |
| `sd_set_session_cookie` | Service Desk | Set the local auth session cookie (validated via a live probe before acceptance); touches no ticket data | CONTROL | LOW | NO |

## Knowledge Base (`services/mcp/kb/`) — 3 tools

| Tool | Domain | Purpose | Capability | Risk | Approval |
|---|---|---|---|---|---|
| `kb_search_noc` | Knowledge Base | Search the 'noc' knowledge base (EV/OCPP reference, runbooks) with scope isolation bound server-side | READ | LOW | NO |
| `kb_list_scopes` 🆕 | Knowledge Base | List configured knowledge scopes and search profiles from scopes.yaml (configuration shape only) | READ | LOW | NO |
| `kb_ingest_status` 🆕 | Knowledge Base | Report live index state (documents indexed per collection); never triggers ingestion | READ | LOW | NO |

## Docgen (`services/mcp/docgen/`) — 3 tools

| Tool | Domain | Purpose | Capability | Risk | Approval |
|---|---|---|---|---|---|
| `create_excel` | Document Generation | Generate a downloadable .xlsx file from tabular data | WRITE | LOW | NO |
| `create_word` | Document Generation | Generate a downloadable .docx file from report data | WRITE | LOW | NO |
| `create_pdf` | Document Generation | Generate a downloadable .pdf file from report data | WRITE | LOW | NO |

## Platform Intelligence (`services/mcp/platform/`) — 8 tools

| Tool | Domain | Purpose | Capability | Risk | Approval |
|---|---|---|---|---|---|
| `platform_health` | Platform Intelligence | Liveness check + whether the read-only repo mount is present | READ | LOW | NO |
| `platform_project` | Platform Intelligence | Declared VERSION, top-level layout, list of MCP servers under services/mcp/ | READ | LOW | NO |
| `platform_python` | Platform Intelligence | This server's Python runtime + each MCP service's declared requirements.txt | READ | LOW | NO |
| `platform_git` | Platform Intelligence | Current branch, latest commit, working-tree cleanliness | READ | LOW | NO |
| `platform_filesystem` | Platform Intelligence | Shallow directory listing (name/type/size only, dotfiles excluded) | READ | LOW | NO |
| `platform_api` | Platform Intelligence | This server's own name/transport/tool list (not cross-server discovery) | READ | LOW | NO |
| `platform_mcp_discovery` 🆕 | Platform Intelligence | Static source scan of services/mcp/* for @mcp.tool declarations across all servers | READ | LOW | NO |
| `platform_config_validate` 🆕 | Platform Intelligence | Cross-check docker-compose.yml / librechat.yaml / services/mcp/* for configuration drift | READ | LOW | NO |

