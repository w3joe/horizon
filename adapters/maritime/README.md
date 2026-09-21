# Maritime replay adapters

The CANOE adapter streams and hash-verifies the acquired `imu/imu.csv` and
`motor/power.csv` files. It preserves each UTC microsecond source timestamp,
maps both streams to one relative replay clock, and emits their actual recorded
values in timestamp order. The IMU sample is partial and its primary data
reference does not state numerical measurement units or a PTP error bound, so
the emitted capability remains degraded and records those limitations. Motor
power is reported in watts, as specified by the CANOE data reference, but is
not treated as rudder or thrust-position feedback and cannot establish a
maneuvering capability bound.

Large sensor artifacts remain outside Git. Radar and camera acquisitions stay
raw references until their calibration and image interpretation are validated.
Postprocessed CANOE navigation truth is excluded from online inputs.
