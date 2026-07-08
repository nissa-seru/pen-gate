// ============================================================
//  AxiDraw pen-lift servo horn — parametric, for MG90S / SG90
// ============================================================
//  Cam-follower design: a single arm with a peg / roller / bearing
//  at the tip that sweeps under the pen-carriage aluminum tab to
//  lift it. Pen returns down under gravity when the servo swings
//  the horn back.
//
//  Center is a smooth press-fit bore for the servo spline
//  (teeth cut into the plastic on first install).
//
//  How to use:
//    1. Adjust PARAMETERS block below.
//    2. Preview: F5.  Render solid: F6.  Export: File > Export > STL.
//    3. Print hub-down, arm flat on the bed.
//
// ============================================================
//  PARAMETERS
// ============================================================

/* [Servo — MG90S defaults] */
spline_od       = 4.85;   // Outer diameter of splined shaft (mm) — nominal MG90S spec
spline_bore     = 4.95;   // Center hole ID; smaller = tighter press-fit.
                          //   v1 4.55: too tight to press on.
                          //   v2 5.05: seated but slightly loose.
                          //   v3 4.95: 0.1 tighter than v2 for a firmer bite.
shaft_height    = 5.0;    // Height of splined shaft above the servo case (mm)
boss_od         = 6.0;    // Diameter of the non-splined boss under the spline (mm)
boss_height     = 0.0;    // Height of that boss. This servo has NO boss → 0.

/* [Center screw — M2.5 self-tapper] */
screw_head_od   = 5.0;    // Screw head diameter, for counterbore
screw_head_h    = 1.8;    // Head recess depth
screw_shaft_od  = 2.8;    // Clearance for the M2.5 shaft (2.5 nominal + slop)

/* [Arm — stock AxiDraw pen-lift geometry] */
// Arm tip = roller_position (defined below). Adjust roller_position to
// change effective arm length as well as cam radius.
// Arm tip width matches peg diameter so the arm tapers cleanly into the
// roller with no surrounding shoulder.
arm_width       = 4.0;    // Arm tip width (mm) — set equal to peg_od
arm_thickness   = 3.7;    // Arm vertical thickness (mm)
                          //   v3 at 3.0 was 0.7 mm below the top of the spline — the M2.5
                          //   screw head slid past into the shaft and wouldn't preload.
                          //   3.7 puts the horn top flush with the top of the spline gears.
hub_od          = 9.0;    // Outer diameter of the central hub (mm)

/* [Cam follower at tip]
   Position along the arm (distance from spline center).
   Effective cam radius = roller_position + roller_od/2 during lift stroke,
   so lift stroke ≈ 2 * roller_position for a ~180° servo sweep. */
roller_position = 17.0;   // Distance from spline center to roller CENTER (mm)
                          //   v1 19 mm: too long. v2 15 mm: a touch short.
                          //   v3 17 mm: midpoint of the two.

/* Roller/tip mode:
     0 = integrated printed peg (simplest)
     1 = through-hole for a pin (steel wire or small screw); slip a
         bearing or brass sleeve over the pin externally
     2 = pocket for a small ball bearing (recommended if you have one) */
ROLLER_MODE = 0;

// Mode 0: integrated peg dimensions
// Target: total protrusion from horn bottom face to peg tip ≈ 12 mm.
// That means peg_length = 12 - arm_thickness.
peg_od          = 4.0;    // Peg diameter (mm)
peg_length      = 9.0;    // How far it sticks out from the arm's face (mm)
peg_side        = 1;      // Which face: -1 = bottom, +1 = top (opposite servo body)

// Mode 1: through-hole for a pin
pin_hole_d      = 2.2;    // Slip fit for M2 screw or 2 mm wire
pin_boss_od     = 6.0;    // Reinforcement around the pin hole (mm)

// Mode 2: bearing pocket
// Common tiny bearings:  MR52ZZ 2x5x2.5   MR63ZZ 3x6x2.5   MR74ZZ 4x7x2.5
bearing_od      = 6.0;    // Bearing OD (mm)
bearing_thk     = 2.5;    // Bearing width (mm)
bearing_id_pin  = 3.0;    // Center pin diameter under the bearing
bearing_boss_od = 8.5;    // Reinforcement around the bearing pocket

/* [Print quality] */
$fn             = 96;

// ============================================================
//  GEOMETRY
// ============================================================

module arm_2d() {
    hull() {
        circle(d = hub_od);
        translate([roller_position, 0]) circle(d = arm_width);
    }
}

module horn_body() {
    linear_extrude(height = arm_thickness) arm_2d();
}

module center_hole() {
    // Counterbore for screw head
    translate([0, 0, arm_thickness - screw_head_h + 0.01])
        cylinder(h = screw_head_h + 0.02, d = screw_head_od);
    // Through clearance for screw shaft
    translate([0, 0, -0.1])
        cylinder(h = arm_thickness + 0.2, d = screw_shaft_od);
}

module spline_bore_hole() {
    // Press-fit smooth bore + relief for the boss below
    translate([0, 0, -0.01])
        cylinder(h = shaft_height + 0.02, d = spline_bore);
    translate([0, 0, -0.01])
        cylinder(h = boss_height + 0.01, d = boss_od);
}

// -------- Tip variants ---------------------------------------

module tip_peg() {
    // A cylinder sticking out perpendicular to the arm's flat face.
    // peg_side = +1 sits on top of the arm, -1 hangs below.
    z0 = (peg_side > 0) ? arm_thickness : -peg_length;
    translate([roller_position, 0, z0])
        cylinder(h = peg_length, d = peg_od);
}

module tip_pin_hole() {
    // Small reinforcement boss around the through-hole so it doesn't
    // split when you press a pin in.
    translate([roller_position, 0, 0])
        cylinder(h = arm_thickness, d = pin_boss_od);
}
module tip_pin_hole_cut() {
    translate([roller_position, 0, -0.1])
        cylinder(h = arm_thickness + 0.2, d = pin_hole_d);
}

module tip_bearing_boss() {
    translate([roller_position, 0, 0])
        cylinder(h = arm_thickness, d = bearing_boss_od);
}
module tip_bearing_cut() {
    // Bearing pocket sits flush with one face; center pin projects
    // out the other so you have something to press the bearing onto.
    // Pocket entrance from top face, pin sticks down from bottom face.
    translate([roller_position, 0, arm_thickness - bearing_thk + 0.01])
        cylinder(h = bearing_thk + 0.1, d = bearing_od);
    // Through hole for the pin passing through the bore of the bearing
    translate([roller_position, 0, -0.1])
        cylinder(h = arm_thickness + 0.2, d = bearing_id_pin);
}

// ============================================================
//  ASSEMBLY
// ============================================================

union() {
    difference() {
        union() {
            horn_body();
            if (ROLLER_MODE == 1) tip_pin_hole();
            if (ROLLER_MODE == 2) tip_bearing_boss();
        }
        center_hole();
        spline_bore_hole();
        if (ROLLER_MODE == 1) tip_pin_hole_cut();
        if (ROLLER_MODE == 2) tip_bearing_cut();
    }
    if (ROLLER_MODE == 0) tip_peg();
}

// ============================================================
//  NOTES
// ============================================================
//  Print orientation: hub down, arm flat on the bed. Layer lines
//    run through the arm's thickness so bending loads stay in-plane.
//  Layer height: 0.15 mm; 3+ perimeters; 40–60% infill; PLA or PETG.
//  Install: light oil on the servo spline. Press horn straight
//    down until seated flush against the boss; screw down.
//
//  Fit tuning:
//    Horn spins on shaft         → spline_bore -= 0.05
//    Horn cracks on install      → spline_bore += 0.05  (or hub_od += 1)
//    Not enough pen lift         → roller_position += 1..3
//    Too much lift / servo stall → roller_position -= 1..2
//    Roller wears fast (mode 0)  → switch to ROLLER_MODE = 1 or 2
//
