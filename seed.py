"""Load a handful of sample pre-plans and hydrants.

Run standalone:      python seed.py
Or via the CLI:      flask --app run seed-db

Idempotent: does nothing if occupancies already exist. Sample locations are
scattered around Montpelier, VT so the map has something to show.
"""
from app import create_app
from app.extensions import db
from app.models import Department, User, Occupancy, Contact, Hazard, Hydrant

DEMO_DEPARTMENT = "Montpelier Fire Department"
DEMO_EMAIL = "admin@example.com"
DEMO_PASSWORD = "changeme"


def seed_database():
    if Occupancy.query.count() > 0:
        return 0

    # A demo department + admin so there's something to log in as.
    dept = Department.query.filter_by(name=DEMO_DEPARTMENT).first()
    if dept is None:
        dept = Department(name=DEMO_DEPARTMENT)
        db.session.add(dept)
        db.session.flush()  # assign dept.id
    if not User.query.filter_by(email=DEMO_EMAIL).first():
        admin = User(email=DEMO_EMAIL, name="Demo Admin",
                     role="admin", department_id=dept.id)
        admin.set_password(DEMO_PASSWORD)
        db.session.add(admin)

    riverside = Occupancy(
        name="Riverside Elementary School", address="120 School St",
        city="Montpelier", state="VT", zip_code="05602",
        latitude=44.2635, longitude=-72.5810,
        occupancy_type="Educational", construction_type="Type II - Non-Combustible",
        building_condition="Good", stories=2, square_footage=48000, year_built=1998,
        roof_construction="Steel bar joist, membrane roof",
        sprinkler_system=True, sprinkler_details="Wet system, riser in NE mechanical room.",
        standpipe_system=False, fire_alarm_system=True,
        fdc_location="North wall, left of main entry",
        knox_box_location="Right of main entrance doors",
        annunciator_location="Main lobby, inside north entrance",
        electric_shutoff_location="Basement electrical room (NE corner)",
        gas_shutoff_location="Exterior, SE corner near loading dock",
        water_supply_notes="Hydrant H-2 at School St & Elm (~150 ft NW).",
        access_notes="Bus loop on north side; ladder access best from playground (south).",
    )
    riverside.contacts = [
        Contact(name="Karen Mills", role="Owner", phone="802-555-0140",
                notes="School district facilities director"),
        Contact(name="Dan Roy", role="Key Holder", phone="802-555-0177"),
    ]

    granite = Occupancy(
        name="Granite Ridge Apartments", address="88 Barre St",
        city="Montpelier", state="VT", zip_code="05602",
        latitude=44.2588, longitude=-72.5731,
        occupancy_type="Residential - Multi Family",
        construction_type="Type V - Wood Frame", building_condition="Fair",
        stories=3, square_footage=22000, year_built=1974,
        roof_construction="Lightweight wood truss",
        sprinkler_system=False, fire_alarm_system=True,
        knox_box_location="Front vestibule, left side",
        electric_shutoff_location="Basement, bottom of front stairwell",
        gas_shutoff_location="Rear exterior, gas meter bank",
        access_notes="No rear apparatus access; standpipe-less garden style.",
    )
    granite.hazards = [
        Hazard(hazard_type="Truss Construction", severity="High",
               location="Roof / attic", description="Lightweight truss — early collapse risk."),
    ]

    hardware = Occupancy(
        name="Main Street Hardware", address="45 Main St",
        city="Montpelier", state="VT", zip_code="05602",
        latitude=44.2601, longitude=-72.5754,
        occupancy_type="Mercantile", construction_type="Type III - Ordinary",
        building_condition="Good", stories=2, year_built=1921,
        roof_construction="Wood joist over brick bearing walls",
        sprinkler_system=True, sprinkler_details="Dry system in unheated rear storage.",
        fire_alarm_system=True, fdc_location="Main St frontage, right of entry",
        knox_box_location="Front door, right side",
        water_supply_notes="Downtown grid, strong supply on Main St main.",
    )

    propane = Occupancy(
        name="Green Mountain Propane & Supply", address="310 Industrial Ln",
        city="Montpelier", state="VT", zip_code="05602",
        latitude=44.2549, longitude=-72.5688,
        occupancy_type="Storage", construction_type="Type II - Non-Combustible",
        building_condition="Good", stories=1, year_built=2005,
        sprinkler_system=False, fire_alarm_system=True,
        knox_box_location="Office entrance, north side",
        gas_shutoff_location="Bulk tank emergency shutoff at tank farm (fenced, SE)",
        hazards_summary="30,000 gal bulk propane storage on site.",
        access_notes="Wide gravel yard; keep apparatus upwind of tank farm.",
    )
    propane.hazards = [
        Hazard(hazard_type="Compressed / LP Gas", severity="Critical",
               location="SE tank farm", description="30,000 gal bulk propane. BLEVE risk."),
        Hazard(hazard_type="Flammable / Combustible Storage", severity="High",
               location="Warehouse bay 2", description="Palletized cylinders."),
    ]
    propane.contacts = [
        Contact(name="Green Mtn Propane (24h)", role="Emergency Contact",
                phone="802-555-0199", notes="After-hours dispatch / shutoff crew"),
    ]

    grange = Occupancy(
        name="Old Grange Hall", address="7 Elm St",
        city="Montpelier", state="VT", zip_code="05602",
        latitude=44.2662, longitude=-72.5779,
        occupancy_type="Assembly", construction_type="Type V - Wood Frame",
        building_condition="Poor", stories=2, year_built=1889,
        roof_construction="Balloon-frame; open stud channels floor to attic",
        sprinkler_system=False, fire_alarm_system=False,
        access_notes="Historic; balloon-frame vertical fire spread. Limited hydrant coverage.",
        hazards_summary="Balloon-frame construction, no detection, aging wiring.",
    )
    grange.hazards = [
        Hazard(hazard_type="Structural / Collapse", severity="High",
               location="Whole structure", description="Aging Type V, poor condition."),
        Hazard(hazard_type="Electrical", severity="Medium",
               location="Basement panel", description="Knob-and-tube remnants suspected."),
    ]

    occupancies = [riverside, granite, hardware, propane, grange]

    hydrants = [
        Hydrant(label="H-1 Main & State", latitude=44.2607, longitude=-72.5760,
                flow_gpm=1650, static_pressure=72, residual_pressure=60,
                hydrant_type="Dry barrel", size_inches='4½" + 2×2½"'),
        Hydrant(label="H-2 School & Elm", latitude=44.2641, longitude=-72.5802,
                flow_gpm=1180, static_pressure=64, residual_pressure=48,
                hydrant_type="Dry barrel"),
        Hydrant(label="H-3 Barre St", latitude=44.2585, longitude=-72.5719,
                flow_gpm=920, static_pressure=58, hydrant_type="Dry barrel"),
        Hydrant(label="H-4 Industrial Ln", latitude=44.2556, longitude=-72.5701,
                flow_gpm=1400, static_pressure=66, hydrant_type="Wet barrel"),
        Hydrant(label="H-5 Elm St", latitude=44.2669, longitude=-72.5772,
                flow_gpm=430, static_pressure=40, hydrant_type="Dry barrel",
                notes="Rural edge of grid — weak supply."),
        Hydrant(label="H-6 Main & 3rd", latitude=44.2596, longitude=-72.5741,
                flow_gpm=1550, static_pressure=70, hydrant_type="Dry barrel"),
        Hydrant(label="H-7 State St", latitude=44.2619, longitude=-72.5735,
                flow_gpm=1050, hydrant_type="Dry barrel"),
        Hydrant(label="H-8 Industrial (out)", latitude=44.2540, longitude=-72.5675,
                flow_gpm=1300, hydrant_type="Wet barrel",
                in_service=False, notes="Tagged out — barrel drain frozen."),
    ]

    # Stamp every sample record with the demo department.
    for record in occupancies + hydrants:
        record.department_id = dept.id

    db.session.add_all(occupancies + hydrants)
    db.session.commit()
    return len(occupancies) + len(hydrants)


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        db.create_all()  # convenience for standalone runs on a fresh DB
        n = seed_database()
    if n:
        print(f"Seeded {n} records.")
        print(f"Demo login:  {DEMO_EMAIL}  /  {DEMO_PASSWORD}")
    else:
        print("Already seeded — no changes.")
