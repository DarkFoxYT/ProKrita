"""Pure PC geometry regressions; Python/Krita/Qt are not needed beyond Python itself."""
import math
from pathlib import Path
import sys
import types
import unittest

# Import geometry without executing the installed plugin's Krita entry point.
package = types.ModuleType("prokrita")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "prokrita")]
sys.modules["prokrita"] = package
from prokrita.shapes import fit_shape


class ShapeIntentTests(unittest.TestCase):
    def test_round_and_elongated_loops(self):
        for radius in (16, 60, 180):
            for angle in (0, .47, 1.2):
                for ratio, expected in ((1., "Circle"), (.84, "Circle"), (.55, "Oval")):
                    for jitter in (0., .018):
                        with self.subTest(radius=radius, angle=angle, ratio=ratio, jitter=jitter):
                            points = []
                            for i in range(241):
                                t = 2*math.pi*(i/240)**1.4
                                noise = 1+jitter*math.sin(13*t)
                                x, y = radius*math.cos(t)*noise, radius*ratio*math.sin(t)*noise
                                points.append((200+x*math.cos(angle)-y*math.sin(angle),
                                               200+x*math.sin(angle)+y*math.cos(angle)))
                            result = fit_shape(points)
                            self.assertIsNotNone(result)
                            self.assertEqual(result[0], expected)
                            if expected == "Circle":
                                center, a, b = result[1]
                                self.assertAlmostEqual(math.dist(center, a), math.dist(center, b))

    def test_other_shapes(self):
        for points, kind in (
            ([(0, 0), (40, 1), (90, 0)], "Line"),
            ([(0, 0), (100, 0), (50, 80), (0, 0)], "Triangle"),
            ([(0, 0), (100, 0), (100, 80), (0, 80), (0, 0)], "Rectangle"),
            ([(0, 0), (50, 0), (50, 60), (110, 60)], "Polyline"),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(fit_shape(points)[0], kind)


if __name__ == "__main__":
    unittest.main()
