#!/bin/bash
# MacFeel: read the pointer-acceleration settings macOS actually uses.
#
# Run on the Mac, with the mouse connected:
#     bash mac-probe.sh > mac-probe.txt
# Read-only: it prints settings and HID properties and changes nothing.
# Send mac-probe.txt back (it contains no personal data beyond device names).

echo "=== system"
sw_vers 2>/dev/null
uname -m

echo
echo "=== user defaults (Tracking speed slider etc.)"
for key in com.apple.mouse.scaling com.apple.scrollwheel.scaling com.apple.mouse.linear \
           com.apple.mouse.scrollingAcceleration com.apple.swipescrolldirection; do
    printf '%s = %s\n' "$key" "$(defaults read -g "$key" 2>&1)"
done

echo
echo "=== hidutil (event system view)"
for key in HIDMouseAcceleration HIDPointerAcceleration HIDMouseScrollAcceleration \
           HIDScrollAcceleration HIDPointerResolution HIDPointerReportRate \
           HIDUseLinearScalingMouseAcceleration HIDPointerAccelerationType \
           HIDPointerAccelerationMultiplier; do
    printf '%s = %s\n' "$key" "$(hidutil property --get "$key" 2>&1 | tr -s ' \n' ' ')"
done

echo
echo "=== hidutil list (pointing devices)"
hidutil list 2>/dev/null | awk 'NR==1 || /Mouse|mouse|MX|Logi|Pointer/'

echo
echo "=== IORegistry: every object that carries acceleration-related properties"
# Prints each object's header line plus its relevant properties, but only for
# objects that carry at least one acceleration-related property.
KEYS='Product|Transport|VendorID|ProductID|PrimaryUsage|PrimaryUsagePage|ReportInterval|IOClass|HIDAccelCurves|HIDTrackingAccelCurves|HIDUserPointerAccelCurves|HIDPointerResolution|HIDPointerReportRate|HIDMouseAcceleration|HIDPointerAcceleration|HIDPointerAccelerationType|HIDPointerAccelerationTable|HIDPointerAccelerationMultiplier|HIDPointerAccelerationAlgorithm|HIDUseLinearScalingMouseAcceleration|HIDScrollResolution|HIDScrollResolutionX|HIDScrollResolutionY|HIDScrollReportRate|HIDScrollAccelerationTable|HIDScrollAccelerationTableY|HIDScrollAccelerationType|HIDMouseScrollAcceleration|HIDScrollAcceleration|HIDScrollAccelCurves'
ioreg -l -w0 | awk -v keys="^[ |]*\"($KEYS)\" = " '
    function flush() {
        if (block ~ /"(HID(Accel|Pointer|Mouse|Scroll|Tracking|User|UseLinear)|ReportInterval)/) printf "%s\n", block
        block = ""
    }
    /\+-o / { flush(); block = $0 "\n"; next }
    $0 ~ keys { block = block $0 "\n" }
    END { flush() }'
