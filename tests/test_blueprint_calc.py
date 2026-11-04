"""Quality-curve maths, checked against the values the game data implies.

The curves below are copied from the generated recipe file, one per craftable
type, so a change to the shared calculator cannot quietly break one category.
Run with: python -m unittest discover -s tests
"""
import unittest

from app import blueprint_calc


def _material(*modifiers, quantity=1.0):
    return {"quantity": quantity, "modifiers": list(modifiers)}


def _modifier(key, start, end, at_start, at_end, name=None, additive=False):
    return {
        "property": key,
        "name": name or key,
        "start": start,
        "end": end,
        "atStart": at_start,
        "atEnd": at_end,
        "additive": additive,
    }


PIPS = "itemresource_powergeneration"
COOLANT = "itemresource_coolantgeneration"
QUANTUM_SPEED = "quantum_speed"
QUANTUM_FUEL = "quantum_fuelrequirement"
SHIELD = "shield_maxhealth"
MITIGATION = "armor_damagemitigation"
DAMAGE = "weapon_damage"


class SegmentTests(unittest.TestCase):
    def test_segment_is_selected_not_multiplied(self):
        # A two-part curve: 0-500 rises 0.8 -> 1.0, 501-1000 rises 1.0 -> 1.2.
        material = _material(
            _modifier(QUANTUM_SPEED, 0, 500, 0.8, 1.0),
            _modifier(QUANTUM_SPEED, 501, 1000, 1.0, 1.2),
        )
        result = blueprint_calc.changes([material], [834])[QUANTUM_SPEED]
        self.assertAlmostEqual(result["percent"], 13.3467, places=3)

    def test_segment_boundary_uses_the_lower_segment(self):
        material = _material(
            _modifier(COOLANT, 0, 500, 0.85, 1.0),
            _modifier(COOLANT, 501, 1000, 1.0, 1.15),
        )
        self.assertAlmostEqual(
            blueprint_calc.changes([material], [500])[COOLANT]["percent"],
            0.0, places=6)
        self.assertAlmostEqual(
            blueprint_calc.changes([material], [501])[COOLANT]["percent"],
            0.0, places=6)

    def test_quality_above_the_last_segment_clamps(self):
        material = _material(
            _modifier(COOLANT, 0, 500, 0.85, 1.0),
            _modifier(COOLANT, 501, 1000, 1.0, 1.15),
        )
        self.assertAlmostEqual(
            blueprint_calc.changes([material], [1000])[COOLANT]["percent"],
            15.0, places=6)


class PowerPlantTests(unittest.TestCase):
    def test_pips_are_summed_as_whole_numbers(self):
        # JS-500: Beryl at 759 and Savrilium at 735 each contribute +1 pip.
        beryl = _material(
            _modifier(PIPS, 700, 799, 1, 1, "Power Pips", additive=True))
        savrilium = _material(
            _modifier(PIPS, 700, 799, 1, 1, "Power Pips", additive=True))
        result = blueprint_calc.changes([beryl, savrilium], [759, 735])[PIPS]
        self.assertIsNone(result["percent"])
        self.assertEqual(result["delta"], 2)

    def test_a_zero_band_stays_zero(self):
        # Some power plants use 0-399:-1, 400-899:0, 900-1000:+1. A quality of
        # 834 must add nothing; the old importer turned these zeros into 1.0.
        bands = [
            _modifier(PIPS, 0, 399, -1, -1, "Power Pips", additive=True),
            _modifier(PIPS, 400, 899, 0, 0, "Power Pips", additive=True),
            _modifier(PIPS, 900, 1000, 1, 1, "Power Pips", additive=True),
        ]
        result = blueprint_calc.changes([_material(*bands)], [834])[PIPS]
        self.assertEqual(result["delta"], 0)
        low = blueprint_calc.changes([_material(*bands)], [12])[PIPS]
        self.assertEqual(low["delta"], -1)


class ArmourTests(unittest.TestCase):
    def test_mitigation_multiplies_across_ingredients(self):
        carapace = _material(_modifier(MITIGATION, 0, 1000, 0.9, 1.1))
        liner = _material(_modifier(MITIGATION, 0, 1000, 0.95, 1.05))
        result = blueprint_calc.changes([carapace, liner], [1000, 1000])
        self.assertAlmostEqual(result[MITIGATION]["percent"], 15.5, places=6)
        low = blueprint_calc.changes([carapace, liner], [0, 0])
        self.assertAlmostEqual(low[MITIGATION]["percent"], -14.5, places=6)

    def test_temperatures_are_separate_properties(self):
        material = _material(
            _modifier("armor_temperaturemin", 0, 1000, 0.8, 1.2),
            _modifier("armor_temperaturemax", 0, 1000, 0.8, 1.2),
        )
        result = blueprint_calc.changes([material], [1000])
        self.assertAlmostEqual(
            result["armor_temperaturemin"]["percent"], 20.0, places=6)
        self.assertAlmostEqual(
            result["armor_temperaturemax"]["percent"], 20.0, places=6)


class WeaponTests(unittest.TestCase):
    def test_damage_multiplies_across_slots(self):
        # Vendetta HMG frame and barrels at quality 750.
        frame = _material(
            _modifier(DAMAGE, 0, 499, 0.95, 1.0),
            _modifier(DAMAGE, 500, 1000, 1.0, 1.05),
        )
        barrels = _material(
            _modifier(DAMAGE, 0, 499, 0.85, 1.0),
            _modifier(DAMAGE, 500, 1000, 1.0, 1.15),
        )
        result = blueprint_calc.changes([frame, barrels], [750, 750])
        self.assertAlmostEqual(result[DAMAGE]["percent"], 10.1875, places=4)

    def test_duplicate_recoil_modifiers_do_not_double(self):
        # P6-LR: frame and stock are both 1.2 -> 0.8. The second slot applies to
        # what the first left, so the pair is -36%, not -40%.
        frame = _material(_modifier("weapon_recoil_kick", 0, 1000, 1.2, 0.8))
        stock = _material(_modifier("weapon_recoil_kick", 0, 1000, 1.2, 0.8))
        result = blueprint_calc.changes([frame, stock], [1000, 1000])
        self.assertAlmostEqual(
            result["weapon_recoil_kick"]["percent"], -36.0, places=6)

    def test_recoil_curves_are_separate_properties(self):
        frame = _material(
            _modifier("weapon_recoil_kick", 0, 1000, 1.2, 0.8),
            _modifier("weapon_recoil_handling", 0, 1000, 1.2, 0.8),
        )
        result = blueprint_calc.changes([frame], [1000])
        self.assertEqual(set(result), {"weapon_recoil_kick",
                                       "weapon_recoil_handling"})
        self.assertAlmostEqual(
            result["weapon_recoil_kick"]["percent"], -20.0, places=6)


class ShieldAndQuantumTests(unittest.TestCase):
    def test_shield_health_multiplies(self):
        slots = [_material(_modifier(SHIELD, 0, 1000, 0.8, 1.2))
                 for _ in range(3)]
        result = blueprint_calc.changes(slots, [500, 500, 500])
        self.assertAlmostEqual(result[SHIELD]["percent"], 0.0, places=6)
        perfect = blueprint_calc.changes(slots, [1000, 1000, 1000])
        self.assertAlmostEqual(perfect[SHIELD]["percent"], 72.8, places=6)

    def test_fuel_burn_falls_with_quality(self):
        material = _material(
            _modifier(QUANTUM_FUEL, 0, 500, 1.2, 1.0),
            _modifier(QUANTUM_FUEL, 501, 1000, 1.0, 0.8),
        )
        result = blueprint_calc.changes([material], [1000])[QUANTUM_FUEL]
        self.assertAlmostEqual(result["percent"], -20.0, places=6)


class AverageQualityTests(unittest.TestCase):
    def test_quality_is_weighted_by_quantity(self):
        materials = [
            {"quantity": 4.0, "modifiers": []},
            {"quantity": 1.0, "modifiers": []},
        ]
        self.assertAlmostEqual(
            blueprint_calc.average_quality([1000, 500], materials), 900.0)


if __name__ == "__main__":
    unittest.main()
