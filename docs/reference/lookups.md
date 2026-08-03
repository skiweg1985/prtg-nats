---
title: PRTG lookups
role: developer
updated: 2026-08-03
---

# PRTG lookups for reference

The lookup files shipped with PRTG, so that while building a sensor you can
check which ones exist and which state they trigger. **This repository does
not deploy them** - there is no mechanism for it, and PRTG brings them along
anyway. They live here as reference material under [lookups/](../lookups/).

Installation-specific lookups are deliberately absent: the overview is meant
to hold for any PRTG installation, not for a particular one.

As of: 255 files.

## Why the state matters more than the text

A lookup determines not only what appears in the channel column, but also
whether the sensor turns red because of it.
`prtg.standardlookups.yesno.stateyesok` maps `1` to *Yes (OK)* and `2` to
*No (Error)* - it does not know a `0` at all; PRTG then shows "undefined
lookup value" and turns that into a mere warning. A channel that is meant to
explain rather than complain therefore needs a lookup without an error
state.

## Yes/no and boolean

The practically most important ones, with their complete mapping.

| Lookup | Values | alarm-free |
| --- | --- | --- |
| `oid.paessler.hplaserjet.jamstatus` | `0` = No Jam Detected (OK), `1` = Paper Jam Detected (Error) | no |
| `oid.paessler.hplaserjet.paperstatus` | `0` = Paper Okay (OK), `1` = Out Of Paper or No Cassette Loaded (Error), `2` = Manual Paper Feed Required (Error) | no |
| `oid.paessler.hplaserjet.tonerstatus` | `0` = Toner Okay (OK), `1` = Toner Low (Warning), `2` = No Toner Cartridge Loaded (Error) | no |
| `prtg.standardlookups.QNAP.SMARTStatus` | `0` = Good (OK), `1` = Bad (Error), `2` = Normal (OK) | no |
| `prtg.standardlookups.Synology.Status` | `1` = Normal (OK), `2` = Failed (Error) | no |
| `prtg.standardlookups.boolean.statefalseok` | `0` = False (OK), `1` = True (Error) | no |
| `prtg.standardlookups.boolean.statetrueok` | `0` = False (Error), `1` = True (OK) | no |
| `prtg.standardlookups.buffalo.ts.nasiscsistatus` | `-1` = Unknown (Warning), `1` = Connected (OK), `2` = Standing By (OK) | no |
| `prtg.standardlookups.buffalo.ts.nasrpsustatus` | `-1` = Unknown (Warning), `1` = Fine (OK), `2` = Broken (Error) | no |
| `prtg.standardlookups.cisco.truthvalue` | `1` = True (OK), `2` = False (OK) | yes |
| `prtg.standardlookups.commonsaas.services` | `` = Not Checked (OK), `` = Not Available (OK), `` = Available (OK) | yes |
| `prtg.standardlookups.dell.equallogic.diskhealth` | `0` = Status Not Available (Error), `1` = Ok (OK), `2` = Tripped (Warning) | no |
| `prtg.standardlookups.dell.equallogic.powersupplystatus` | `1` = On and Operating (OK), `2` = No AC Power (Error), `3` = Failed or No Data (Error) | no |
| `prtg.standardlookups.exchangedag.yesno.allstatesok` | `1` = Yes (OK), `0` = No (OK) | yes |
| `prtg.standardlookups.exchangedag.yesno.statenook` | `0` = No (OK), `1` = Yes (Error) | no |
| `prtg.standardlookups.exchangedag.yesno.stateyesok` | `1` = Yes (OK), `0` = No (Error) | no |
| `prtg.standardlookups.exchangedag.yesno.stateyeswarning` | `0` = No (OK), `1` = Yes (Warning) | no |
| `prtg.standardlookups.fujitsu.fsc-raid-mib.svrlogicaldrive.svrlogicaldriveinitstatus` | `1` = Unknown (None), `2` = Initialized (OK), `3` = Not Initialized (None) | yes |
| `prtg.standardlookups.fujitsu.fsc-raid-mib.svrphysicaldevice.svrphysicaldeviceconfigureddisk` | `1` = False (None), `2` = True (OK) | yes |
| `prtg.standardlookups.fujitsu.fsc-raid-mib.svrphysicaldevice.svrphysicaldeviceforeignconfig` | `1` = False (OK), `2` = True (Warning) | no |
| `prtg.standardlookups.fujitsu.fsc-servercontrol2-mib.sc2memorymodule.sc2memmoduleapproved` | `1` = Unknown (None), `2` = No (None), `3` = Yes (OK) | yes |
| `prtg.standardlookups.hyperv.virtualserverstate` | `0` = Unknown (None), `1` = Running (OK), `2` = Stopped (Error) | no |
| `prtg.standardlookups.juniper.fanstatus` | `0` = Fail (Error), `1` = Good (OK), `2` = Not installed (None) | no |
| `prtg.standardlookups.netapp.nichealth` | `1` = Healthy (OK), `2` = Unhealthy (Error), `3` = Not Available (None) | no |
| `prtg.standardlookups.netapp.notavailable` | `` = Not Available (OK), `` = Valid (OK), `` = Valid (OK) | yes |
| `prtg.standardlookups.netapp.sparestate` | `` = Node has insufficient spare disks (Error), `` = Warning (Warning), `` = OK (OK) | no |
| `prtg.standardlookups.netapp.takeoverstatus` | `1` = Up (OK), `2` = Unknown (Warning), `3` = Failed (Error) | no |
| `prtg.standardlookups.oracle.tablespace.status` | `0` = AVAILABLE (OK), `1` = INVALID (Error), `100` = UNKNOWN (Error) | no |
| `prtg.standardlookups.paessler.cisco.admin_status` | `0` = Unknown (Error), `1` = Enabled (Ok), `2` = Disabled (None) | no |
| `prtg.standardlookups.paessler.ciscomeraki.lookup_license_model` | `0` = Unknown (Warning), `1` = Co-Termination (Ok), `2` = Per-Device (Ok) | no |
| `prtg.standardlookups.paessler.dns.lookup_records_found` | `0` = No (Error), `1` = Yes (Ok) | no |
| `prtg.standardlookups.paessler.fortigate.lookup_conserve_mode` | `0` = Unknown (None), `1` = Inactive (Ok), `2` = Active (Error) | no |
| `prtg.standardlookups.paessler.momodns.lookup_records_found` | `0` = No (Error), `1` = Yes (Ok) | no |
| `prtg.standardlookups.paessler.netapp.healthy` | `0` = Channel Value Not Set (None), `1` = Healthy (Ok), `2` = Unhealthy (Error) | no |
| `prtg.standardlookups.paessler.netapp.is_home` | `0` = Channel Value Not Set (None), `1` = At Home (Ok), `2` = Not At Home (Warning) | no |
| `prtg.standardlookups.paessler.netapp.lif_state` | `0` = Channel Value Not Set (None), `1` = Up (Ok), `2` = Down (Error) | no |
| `prtg.standardlookups.paessler.netapp.nic_state` | `0` = Channel Value Not Set (None), `1` = Up (Ok), `2` = Down (Error) | no |
| `prtg.standardlookups.paessler.netapp.temperature_state` | `0` = Channel Value Not Set (None), `1` = Normal (Ok), `2` = Over (Error) | no |
| `prtg.standardlookups.paessler.opcua.negative_boolean_lookup` | `0` = False (Ok), `1` = True (Error) | no |
| `prtg.standardlookups.paessler.opcua.positive_boolean_lookup` | `0` = False (Error), `1` = True (Ok) | no |
| `prtg.standardlookups.paessler.opcua.self_signed_certificate` | `0` = No (Ok), `1` = Yes (Ok) | yes |
| `prtg.standardlookups.paessler.proxmox.proxmox_status_lookup` | `0` = Unknown (None), `1` = Running (OK), `2` = Stopped (Warning) | no |
| `prtg.standardlookups.paessler.snmp.sensor_behavior_down_on_found` | `0` = String Not Found (None), `1` = String Found (Error) | no |
| `prtg.standardlookups.paessler.snmp.sensor_behavior_down_on_not_found` | `0` = String Not Found (Error), `1` = String Found (None) | no |
| `prtg.standardlookups.paessler.snmp.sensor_behavior_warning_on_found` | `0` = String Not Found (None), `1` = String Found (Warning) | no |
| `prtg.standardlookups.paessler.snmp.sensor_behavior_warning_on_not_found` | `0` = String Not Found (Warning), `1` = String Found (None) | no |
| `prtg.standardlookups.paessler.tls.signed_by` | `0` = Unknown (None), `1` = Self-Signed (OK), `2` = CA-Signed (OK) | yes |
| `prtg.standardlookups.paessler.tls.trust_status` | `0` = Unknown (None), `1` = Trusted (OK), `2` = Not Trusted (Warning) | no |
| `prtg.standardlookups.paessler.veeam.yesno_no_is_error` | `0` = No (Error), `1` = Yes (Ok) | no |
| `prtg.standardlookups.paessler.veeam.yesno_no_is_ok` | `0` = No (None), `1` = Yes (Ok) | yes |
| `prtg.standardlookups.sigfox.keepalive` | `0` = No (None), `1` = Yes (OK) | yes |
| `prtg.standardlookups.sshsan.health` | `0` = Ok (OK), `2` = Fault (Error), `4` = Not Available (Error) | no |
| `prtg.standardlookups.sslcertificatesensor.revoked` | `-1` = Unable to check revocation status (Warning), `0` = No (OK), `1` = Yes (Error) | no |
| `prtg.standardlookups.sslcertificatesensor.selfsigned` | `0` = No (OK), `1` = Yes (OK) | yes |
| `prtg.standardlookups.sslcertificatesensor.trustedroot` | `0` = Yes (OK), `1` = No (Warning) | no |
| `prtg.standardlookups.sslsensor.security` | `3` = Only Strong Protocols Available (OK), `2` = Weak Protocols Available (Warning), `1` = No Secure Protocol Available (Error) | no |
| `prtg.standardlookups.sslsensor.security.compatibility` | `` = OK (Compatibility Mode) (OK), `` = No Secure Protocol Available (Error) | no |
| `prtg.standardlookups.yesno.statenook` | `1` = No (OK), `2` = Yes (Error) | no |
| `prtg.standardlookups.yesno.statenookna` | `1` = No (OK), `2` = Yes (Error), `3` = Not Available (OK) | no |
| `prtg.standardlookups.yesno.stateyesok` | `1` = Yes (OK), `2` = No (Error) | no |

## All shipped lookups

| Lookup | Values | States |
| --- | --- | --- |
| `oid.paessler.hplaserjet.jamstatus` | 2 | Error, Ok |
| `oid.paessler.hplaserjet.paperstatus` | 3 | Error, Ok |
| `oid.paessler.hplaserjet.tonerstatus` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.Google.Gsa.Health` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.QNAP.HDStatus` | 5 | Error, Ok |
| `prtg.standardlookups.QNAP.SMARTStatus` | 3 | Error, Ok |
| `prtg.standardlookups.QNAP.VolStatus` | 5 | Error, Ok, Warning |
| `prtg.standardlookups.Synology.DiskStatus` | 5 | Error, Ok, Warning |
| `prtg.standardlookups.Synology.RaidStatus` | 21 | Error, Ok, Warning |
| `prtg.standardlookups.Synology.Status` | 2 | Error, Ok |
| `prtg.standardlookups.access.status` | 5 | Ok |
| `prtg.standardlookups.activeinactive.stateactiveok` | 2 | Error, Ok |
| `prtg.standardlookups.activeinactive.stateless` | 3 | None |
| `prtg.standardlookups.apc-mib.upsbattery.upsbatteryteststatus` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.aws.statevalue` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.aws.status` | 2 | Error, Ok |
| `prtg.standardlookups.boolean.statefalseok` | 2 | Error, Ok |
| `prtg.standardlookups.boolean.statetrueok` | 2 | Error, Ok |
| `prtg.standardlookups.buffalo.ts.nasarraystatus` | 11 | Ok |
| `prtg.standardlookups.buffalo.ts.nasdisksmartstatus` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.buffalo.ts.nasdiskstatus` | 15 | Error, Ok, Warning |
| `prtg.standardlookups.buffalo.ts.nasfailoverstatus` | 6 | Ok, Warning |
| `prtg.standardlookups.buffalo.ts.nasiscsistatus` | 3 | Ok, Warning |
| `prtg.standardlookups.buffalo.ts.nasisfwupdateavailable` | 4 | Ok, Warning |
| `prtg.standardlookups.buffalo.ts.nasrpsustatus` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.businessprocess.state` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.cisco.ciscoenvmonstate` | 6 | Error, None, Ok, Warning |
| `prtg.standardlookups.cisco.cucs.cucsequipmentchassisconfigstate` | 9 | Error, Ok, Warning |
| `prtg.standardlookups.cisco.cucs.cucsequipmentchassispoweroperstate` | 9 | Error, Ok, Warning |
| `prtg.standardlookups.cisco.cucs.cucslicensestate` | 6 | Error, None, Ok, Warning |
| `prtg.standardlookups.cisco.cucs.equipmentoperability` | 30 | Error, Ok, Warning |
| `prtg.standardlookups.cisco.cucs.equipmentpowerstate` | 13 | Error, Ok, Warning |
| `prtg.standardlookups.cisco.cucs.equipmentpresence` | 12 | Error, None, Ok, Warning |
| `prtg.standardlookups.cisco.cucs.equipmentsensorthresholdstatus` | 9 | Error, None, Ok, Warning |
| `prtg.standardlookups.cisco.cucs.lsoperstate` | 32 | Error, Ok, Warning |
| `prtg.standardlookups.cisco.sensecode` | 21 | Error, Ok |
| `prtg.standardlookups.cisco.truthvalue` | 2 | Ok |
| `prtg.standardlookups.commonsaas.services` | 3 | Ok |
| `prtg.standardlookups.connectionstate.bothok` | 2 | Ok |
| `prtg.standardlookups.connectionstate.stateonlineok` | 2 | Error, Ok |
| `prtg.standardlookups.dell.dellstatus` | 6 | Error, Ok, Warning |
| `prtg.standardlookups.dell.diskstate` | 23 | Error, None, Ok, Warning |
| `prtg.standardlookups.dell.diskstate_idrac` | 9 | Error, Ok, Warning |
| `prtg.standardlookups.dell.equallogic.availability` | 2 | Error, Ok |
| `prtg.standardlookups.dell.equallogic.diskhealth` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.dell.equallogic.diskstatus` | 13 | Error, Ok, Warning |
| `prtg.standardlookups.dell.equallogic.memberhealthstatus` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.dell.equallogic.memberstatus` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.dell.equallogic.operstatus` | 11 | Error, Ok, Warning |
| `prtg.standardlookups.dell.equallogic.powersupplystatus` | 3 | Error, Ok |
| `prtg.standardlookups.dell.equallogic.raidstatus` | 8 | Error, Ok, Warning |
| `prtg.standardlookups.dell.phydisk.mode` | 6 | Error, Ok, Warning |
| `prtg.standardlookups.dell.phydisk.status` | 6 | Error, Ok, Warning |
| `prtg.standardlookups.disabledenabled.stateenabledok` | 2 | Error, Ok |
| `prtg.standardlookups.disabledenabled.stateless` | 2 | None |
| `prtg.standardlookups.docker.containerstatus` | 5 | Error, Ok, Warning |
| `prtg.standardlookups.emc.health` | 8 | Error, None, Ok, Warning |
| `prtg.standardlookups.emc.lenovo.diskstatus` | 5 | Error, Ok, Warning |
| `prtg.standardlookups.emc.lenovo.raidstatus` | 6 | Error, Ok, Warning |
| `prtg.standardlookups.esxelementhealthsensor.healthstate` | 7 | Error, Ok, Warning |
| `prtg.standardlookups.exampledevice` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.exchangedag.activationstatus` | 5 | Ok, Warning |
| `prtg.standardlookups.exchangedag.contentindexstate` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.exchangedag.status` | 16 | Error, Ok, Warning |
| `prtg.standardlookups.exchangedag.yesno.allstatesok` | 2 | Ok |
| `prtg.standardlookups.exchangedag.yesno.statenook` | 2 | Error, Ok |
| `prtg.standardlookups.exchangedag.yesno.stateyesok` | 2 | Error, Ok |
| `prtg.standardlookups.exchangedag.yesno.stateyeswarning` | 2 | Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-raid-mib.svrctrl.svrctrlbbustatusex` | 7 | Error, None, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-raid-mib.svrctrl.svrctrlstatus` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-raid-mib.svrlogicaldrive.svrlogicaldriveinitstatus` | 3 | None, Ok |
| `prtg.standardlookups.fujitsu.fsc-raid-mib.svrlogicaldrive.svrlogicaldrivestatusex` | 14 | Error, None, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-raid-mib.svrphysicaldevice.svrphysicaldeviceconfigureddisk` | 2 | None, Ok |
| `prtg.standardlookups.fujitsu.fsc-raid-mib.svrphysicaldevice.svrphysicaldeviceforeignconfig` | 2 | Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-raid-mib.svrphysicaldevice.svrphysicaldevicepowerstatus` | 3 | Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-raid-mib.svrphysicaldevice.svrphysicaldevicesmartstatus` | 4 | None, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-raid-mib.svrphysicaldevice.svrphysicaldevicestatusex` | 17 | Error, None, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-raid-mib.svrstatus.svrstatusoverall` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-servercontrol2-mib.sc2cpu.sc2cpustatus` | 8 | Error, None, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-servercontrol2-mib.sc2managementprocessor.sc2spbatterystatus` | 7 | Error, None, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-servercontrol2-mib.sc2memorymodule.sc2memmoduleapproved` | 3 | None, Ok |
| `prtg.standardlookups.fujitsu.fsc-servercontrol2-mib.sc2memorymodule.sc2memmoduleconfiguration` | 8 | None, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-servercontrol2-mib.sc2memorymodule.sc2memmodulestatus` | 8 | Error, None, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-servercontrol2-mib.sc2powersupply.sc2powersupplystatus` | 13 | Error, None, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-servercontrol2-mib.sc2powersupplyredundancyconfiguration.sc2psredundancymodeconfig` | 6 | None, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-servercontrol2-mib.sc2powersupplyredundancyconfiguration.sc2psredundancystatus` | 5 | Error, None, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-servercontrol2-mib.sc2psredundancymode` | 6 | None, Ok, Warning |
| `prtg.standardlookups.fujitsu.fsc-servercontrol2-mib.sc2statuscomponent` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.fujitsu.serverview-status-mib.siestsubsystem.siestsubsystemstatusvalue` | 5 | Error, None, Ok, Warning |
| `prtg.standardlookups.gitlab.buildstatus` | 7 | Error, None, Ok, Warning |
| `prtg.standardlookups.hl7.ackcode` | 3 | Error, Ok |
| `prtg.standardlookups.hp.blade.enclosure.condition` | 5 | Error, None, Ok, Warning |
| `prtg.standardlookups.hp.blade.power` | 6 | None, Ok, Warning |
| `prtg.standardlookups.hp.blade.status` | 5 | Error, None, Ok, Warning |
| `prtg.standardlookups.hp.condition` | 4 | Error, None, Ok |
| `prtg.standardlookups.hp.diskstatus` | 10 | Error, None, Ok, Warning |
| `prtg.standardlookups.hp.eva.state` | 6 | Error, Ok, Warning |
| `prtg.standardlookups.hp.logicaldiskstatus` | 16 | Error, None, Ok, Warning |
| `prtg.standardlookups.hp.memorycontrollererrorstatus` | 17 | Error, None, Ok |
| `prtg.standardlookups.hp.memorymodulestatus` | 11 | Error, None, Ok, Warning |
| `prtg.standardlookups.hp.powersupplystatus` | 17 | Error, Ok |
| `prtg.standardlookups.hp.smartstatus` | 3 | None, Ok, Warning |
| `prtg.standardlookups.hp.status` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.hp.statuswarning` | 4 | None, Ok, Warning |
| `prtg.standardlookups.http.statuscode` | 5 | Error, Ok, Warning |
| `prtg.standardlookups.http.statuscodedetailed` | 65 | Error, Ok, Warning |
| `prtg.standardlookups.hyperv.clusternodestatus` | 4 | Error, None, Ok |
| `prtg.standardlookups.hyperv.communicationstate` | 8 | Error, None, Ok |
| `prtg.standardlookups.hyperv.computerstate` | 10 | Error, None, Ok, Warning |
| `prtg.standardlookups.hyperv.hoststatus` | 11 | Error, None, Ok, Warning |
| `prtg.standardlookups.hyperv.virtualserverstate` | 3 | Error, None, Ok |
| `prtg.standardlookups.hyperv.vmstatus` | 38 | Error, None, Ok, Warning |
| `prtg.standardlookups.ibm.OperationalStatus` | 19 | Error, Ok, Warning |
| `prtg.standardlookups.ibm.overallstatus` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.ibm.psstatus` | 2 | Error, Ok |
| `prtg.standardlookups.ipmi.powersupply` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.juniper.fanstatus` | 3 | Error, None, Ok |
| `prtg.standardlookups.juniper.powerstatus` | 2 | Error, Ok |
| `prtg.standardlookups.lanmanager.servicestate` | 4 | Error, Ok |
| `prtg.standardlookups.liebert.lgppwrbattery.lgppwrbatterychargestatus` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.liebert.lgppwrbatterycapacitystatus` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.liebert.lgpsysstatus.lgpsysselftestresult` | 6 | Error, None, Ok, Warning |
| `prtg.standardlookups.microsoft.applicationpoolstate` | 9 | Error, Ok, Warning |
| `prtg.standardlookups.mqtt.rttstate` | 5 | Error, Ok |
| `prtg.standardlookups.multiplatformprobeconnectionhealthsensor.natsconnectionstate` | 4 | Error, None, Ok |
| `prtg.standardlookups.netapp.aggrstate` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.netapp.batterystate` | 2 | Error, Ok |
| `prtg.standardlookups.netapp.dfstatus` | 10 | Error, Ok |
| `prtg.standardlookups.netapp.fsstatus` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.netapp.healthstate` | 2 | Error, Ok |
| `prtg.standardlookups.netapp.lunalignment` | 6 | Ok, Warning |
| `prtg.standardlookups.netapp.lunstate` | 5 | Error, None, Ok |
| `prtg.standardlookups.netapp.mirrorstate` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.netapp.nichealth` | 3 | Error, None, Ok |
| `prtg.standardlookups.netapp.nodestorageconfiguration` | 10 | Error, Ok, Warning |
| `prtg.standardlookups.netapp.notavailable` | 3 | Ok |
| `prtg.standardlookups.netapp.relationshipstate` | 4 | Error, Ok |
| `prtg.standardlookups.netapp.relationshipstatus` | 11 | Ok, Warning |
| `prtg.standardlookups.netapp.sparestate` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.netapp.takeoverstatus` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.netapp.tempstate` | 2 | Error, Ok |
| `prtg.standardlookups.nutanix-mib.clusterstaus` | 5 | Error, Ok, Warning |
| `prtg.standardlookups.offon.stateless` | 2 | None |
| `prtg.standardlookups.offon.stateonok` | 2 | Error, Ok |
| `prtg.standardlookups.oracle.tablespace.onlinestatus` | 6 | Error, Ok, Warning |
| `prtg.standardlookups.oracle.tablespace.status` | 3 | Error, Ok |
| `prtg.standardlookups.paessler.aws.lookup_alarm_status` | 3 | Error, None, Ok |
| `prtg.standardlookups.paessler.aws.lookup_status_check` | 2 | Error, Ok |
| `prtg.standardlookups.paessler.aws.lookup_volume_status` | 7 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.cisco.admin_status` | 3 | Error, None, Ok |
| `prtg.standardlookups.paessler.cisco.operational_status` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.paessler.ciscomeraki.lookup_license_model` | 3 | Ok, Warning |
| `prtg.standardlookups.paessler.dellemc.lookup_health_status` | 8 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.dns.lookup_records_found` | 2 | Error, Ok |
| `prtg.standardlookups.paessler.exe.status` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.fortigate.lookup_conserve_mode` | 3 | Error, None, Ok |
| `prtg.standardlookups.paessler.hpe3par.lookup_state` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.http.status_code` | 66 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.icmp.reachability_state` | 2 | Error, Ok |
| `prtg.standardlookups.paessler.icmp.reachability_state_reversed` | 2 | Error, Ok |
| `prtg.standardlookups.paessler.microsoft365.overall_component_state` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.paessler.microsoft365.service_component_state` | 10 | None |
| `prtg.standardlookups.paessler.microsoftazure.virtual_machine_status` | 6 | Error, Ok, Warning |
| `prtg.standardlookups.paessler.modbus.lookup_boolean` | 2 | None |
| `prtg.standardlookups.paessler.momodns.lookup_records_found` | 2 | Error, Ok |
| `prtg.standardlookups.paessler.momozoom.lookup_service_states` | 5 | Error, Ok, Warning |
| `prtg.standardlookups.paessler.mqtt.rttstate` | 5 | Error, Ok |
| `prtg.standardlookups.paessler.netapp.aggregate_state` | 11 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.netapp.autogrow_state` | 4 | None, Ok, Warning |
| `prtg.standardlookups.paessler.netapp.container_state` | 5 | Error, None, Ok |
| `prtg.standardlookups.paessler.netapp.container_type` | 13 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.netapp.healthy` | 3 | Error, None, Ok |
| `prtg.standardlookups.paessler.netapp.is_home` | 3 | None, Ok, Warning |
| `prtg.standardlookups.paessler.netapp.lif_state` | 3 | Error, None, Ok |
| `prtg.standardlookups.paessler.netapp.lun_state` | 6 | Error, None, Ok |
| `prtg.standardlookups.paessler.netapp.mirror_state` | 12 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.netapp.nic_state` | 3 | Error, None, Ok |
| `prtg.standardlookups.paessler.netapp.node_health` | 8 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.netapp.nvram_battery` | 10 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.netapp.policy_type` | 7 | None, Ok |
| `prtg.standardlookups.paessler.netapp.storage_configuration_path` | 9 | None, Ok, Warning |
| `prtg.standardlookups.paessler.netapp.temperature_state` | 3 | Error, None, Ok |
| `prtg.standardlookups.paessler.netapp.transfer_status` | 10 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.opcua.negative_boolean_lookup` | 2 | Error, Ok |
| `prtg.standardlookups.paessler.opcua.positive_boolean_lookup` | 2 | Error, Ok |
| `prtg.standardlookups.paessler.opcua.raid_controller_state` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.opcua.self_signed_certificate` | 2 | Ok |
| `prtg.standardlookups.paessler.opcua.server_state` | 8 | Error, Ok, Warning |
| `prtg.standardlookups.paessler.orchestra.lookup_adapter_state` | 5 | Error, Ok, Warning |
| `prtg.standardlookups.paessler.paecloud.cloud_status` | 2 | Error, Ok |
| `prtg.standardlookups.paessler.paecloud.status_code` | 66 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.proxmox.proxmox_status_lookup` | 3 | None, Ok, Warning |
| `prtg.standardlookups.paessler.redfish.lookup_health` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.redfish.lookup_powerstate` | 5 | None, Ok |
| `prtg.standardlookups.paessler.rest.status` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.rest.status_code` | 66 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.rest.string_as_state` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.s7.cpu_status` | 16 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.s7.recent_cycle_time_status` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.s7.restart_state` | 8 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.snmp.sensor_behavior_down_on_found` | 2 | Error, None |
| `prtg.standardlookups.paessler.snmp.sensor_behavior_down_on_not_found` | 2 | Error, None |
| `prtg.standardlookups.paessler.snmp.sensor_behavior_warning_on_found` | 2 | None, Warning |
| `prtg.standardlookups.paessler.snmp.sensor_behavior_warning_on_not_found` | 2 | None, Warning |
| `prtg.standardlookups.paessler.snmp.ups_battery_status` | 5 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.ssh.status` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.tls.name_check` | 4 | Error, None, Ok |
| `prtg.standardlookups.paessler.tls.public_key_strength` | 5 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.tls.revocation_status` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.tls.signed_by` | 3 | None, Ok |
| `prtg.standardlookups.paessler.tls.trust_status` | 3 | None, Ok, Warning |
| `prtg.standardlookups.paessler.veeam.lookup_advanced_status` | 6 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.veeam.lookup_last_result` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.paessler.veeam.lookup_status` | 9 | None, Ok, Warning |
| `prtg.standardlookups.paessler.veeam.yesno_no_is_error` | 2 | Error, Ok |
| `prtg.standardlookups.paessler.veeam.yesno_no_is_ok` | 2 | None, Ok |
| `prtg.standardlookups.paessler.zoom.lookup_service_states` | 5 | Error, Ok, Warning |
| `prtg.standardlookups.radius.status` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.rfc.hardwarestatus` | 5 | Error, Ok, Warning |
| `prtg.standardlookups.rittal.cmc3.devicestatus` | 9 | Error, Ok, Warning |
| `prtg.standardlookups.rittal.cmc3.overallstatus` | 7 | Error, Ok, Warning |
| `prtg.standardlookups.sigfox.device.state` | 8 | Error, Ok, Warning |
| `prtg.standardlookups.sigfox.device.token.state` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.sigfox.keepalive` | 2 | None, Ok |
| `prtg.standardlookups.sip.statuscode` | 72 | Error, Ok, Warning |
| `prtg.standardlookups.snmpprinter.cartridgelevel` | 6 | Error, None, Ok, Warning |
| `prtg.standardlookups.snmpprinter.coverstate` | 6 | Error, None, Ok, Warning |
| `prtg.standardlookups.sshsan.health` | 3 | Error, Ok |
| `prtg.standardlookups.sshsan.status` | 8 | Error, None, Ok, Warning |
| `prtg.standardlookups.sslcertificatesensor.cncheck` | 7 | Error, Ok |
| `prtg.standardlookups.sslcertificatesensor.publickey` | 5 | Error, None, Ok, Warning |
| `prtg.standardlookups.sslcertificatesensor.publickeyecc` | 4 | Error, None, Ok |
| `prtg.standardlookups.sslcertificatesensor.revoked` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.sslcertificatesensor.selfsigned` | 2 | Ok |
| `prtg.standardlookups.sslcertificatesensor.trustedroot` | 2 | Ok, Warning |
| `prtg.standardlookups.sslsensor.acceptokdeniednone` | 2 | None, Ok |
| `prtg.standardlookups.sslsensor.acceptwarndeniedok` | 2 | Ok, Warning |
| `prtg.standardlookups.sslsensor.security` | 3 | Error, Ok, Warning |
| `prtg.standardlookups.sslsensor.security.compatibility` | 2 | Error, Ok |
| `prtg.standardlookups.sslsensor.ssl` | 2 | Ok, Warning |
| `prtg.standardlookups.sslsensor.tls` | 2 | None, Ok |
| `prtg.standardlookups.ups-mib.upsbattery.upsbatterystatus` | 4 | Error, Ok, Warning |
| `prtg.standardlookups.ups-mib.upsoutput.upsoutputsource` | 7 | Error, Ok, Warning |
| `prtg.standardlookups.ups-mib.upstest.upstestresultssummary` | 6 | Error, None, Ok, Warning |
| `prtg.standardlookups.wmi.antivir` | 5 | Error, Ok, Warning |
| `prtg.standardlookups.wmi.battery` | 6 | Error, Ok, Warning |
| `prtg.standardlookups.wmi.battery.ups` | 6 | Error, Ok, Warning |
| `prtg.standardlookups.wmi.diskhealth.health` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.wmi.diskhealth.operationalstatus` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.wmi.service.staterunningok` | 2 | Error, Ok |
| `prtg.standardlookups.wmi.storagepool.health` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.wmi.storagepool.operationalstatus` | 4 | Error, None, Ok, Warning |
| `prtg.standardlookups.yesno.statenook` | 2 | Error, Ok |
| `prtg.standardlookups.yesno.statenookna` | 3 | Error, Ok |
| `prtg.standardlookups.yesno.stateyesok` | 2 | Error, Ok |
