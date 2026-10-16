"""The bearing lookup, frozen from the 2026-10-07 security list (generated; do not edit by hand).

(1M trend, 6M trend, score tier) -> label, each cell the label most of its names carried that day;
`compass.fit` on the same list reproduces it. Frozen so a study declared on it cannot drift as later
lists are saved. The comment on each line is how many labelled names the cell held.
"""

SOURCE = "2026-10-07"

TABLE: dict[tuple[int, int, int], str] = {
    (-4, -4, 0): "strong bear",  # 256
    (-4, -4, 1): "bear",  # 25
    (-4, -4, 2): "weakening",  # 7
    (-4, -3, 0): "strong bear",  # 31
    (-4, -3, 1): "bear",  # 2
    (-4, -2, 0): "strong bear",  # 4
    (-4, 0, 0): "strong bear",  # 2
    (-4, 0, 1): "bear",  # 20
    (-4, 0, 2): "weakening",  # 10
    (-4, 2, 1): "pullback",  # 19
    (-4, 2, 2): "pullback",  # 31
    (-4, 3, 1): "bull",  # 1
    (-4, 4, 1): "pullback",  # 1
    (-4, 4, 2): "pullback",  # 3
    (-3, -4, 0): "strong bear",  # 13
    (-3, -3, 0): "strong bear",  # 2453
    (-3, -3, 1): "bear",  # 75
    (-3, -3, 2): "weakening",  # 29
    (-3, -2, 0): "strong bear",  # 598
    (-3, -2, 1): "bear",  # 423
    (-3, -2, 2): "weakening",  # 79
    (-3, -1, 0): "rally",  # 1
    (-3, -1, 1): "rally",  # 1
    (-3, -1, 2): "neutral",  # 1
    (-3, 0, 0): "strong bear",  # 50
    (-3, 0, 1): "bear",  # 331
    (-3, 0, 2): "weakening",  # 97
    (-3, 1, 1): "bull",  # 1
    (-3, 2, 0): "strong bear",  # 11
    (-3, 2, 1): "pullback",  # 342
    (-3, 2, 2): "pullback",  # 498
    (-3, 4, 1): "bull",  # 1
    (-2, -3, 0): "strong bear",  # 164
    (-2, -3, 1): "bear",  # 15
    (-2, -3, 2): "weakening",  # 12
    (-2, -2, 0): "strong bear",  # 36
    (-2, -2, 1): "bear",  # 5
    (-2, -2, 2): "weakening",  # 7
    (-2, 0, 0): "neutral",  # 22
    (-2, 0, 1): "neutral",  # 16
    (-2, 0, 2): "neutral",  # 11
    (-2, 2, 0): "neutral",  # 24
    (-2, 2, 1): "pullback",  # 102
    (-2, 2, 2): "pullback",  # 160
    (-1, -4, 0): "strong bear",  # 1
    (-1, -3, 0): "strong bear",  # 256
    (-1, -3, 1): "bear",  # 36
    (-1, -3, 2): "weakening",  # 8
    (-1, -2, 0): "strong bear",  # 17
    (-1, -2, 1): "bear",  # 38
    (-1, -2, 2): "weakening",  # 12
    (-1, 0, 0): "neutral",  # 4
    (-1, 0, 1): "bear",  # 52
    (-1, 0, 2): "neutral",  # 21
    (-1, 2, 0): "strong bear",  # 2
    (-1, 2, 1): "pullback",  # 67
    (-1, 2, 2): "pullback",  # 250
    (0, -4, 1): "bear",  # 2
    (0, -3, 0): "strong bear",  # 6
    (0, -2, 1): "bear",  # 1
    (0, -1, 0): "rally",  # 40
    (0, -1, 1): "rally",  # 6
    (0, -1, 2): "rally",  # 4
    (0, 0, 1): "neutral",  # 1
    (0, 0, 2): "weakening",  # 1
    (0, 1, 0): "improving",  # 6
    (0, 1, 1): "pullback",  # 2
    (0, 1, 2): "pullback",  # 5
    (0, 2, 1): "pullback",  # 1
    (0, 2, 2): "pullback",  # 2
    (0, 3, 0): "improving",  # 1
    (0, 3, 1): "bull",  # 2
    (0, 3, 2): "strong bull",  # 1
    (0, 4, 0): "improving",  # 4
    (0, 4, 1): "bull",  # 49
    (0, 4, 2): "strong bull",  # 107
    (1, -3, 0): "strong bear",  # 59
    (1, -3, 1): "bear",  # 20
    (1, -3, 2): "neutral",  # 8
    (1, -2, 0): "strong bear",  # 2
    (1, -2, 1): "bear",  # 6
    (1, -2, 2): "neutral",  # 2
    (1, -1, 0): "strong bear",  # 5
    (1, 0, 0): "neutral",  # 2
    (1, 0, 1): "neutral",  # 11
    (1, 0, 2): "neutral",  # 12
    (1, 1, 0): "strong bear",  # 1
    (1, 1, 1): "neutral",  # 2
    (1, 2, 1): "bull",  # 17
    (1, 2, 2): "strong bull",  # 72
    (1, 4, 0): "improving",  # 1
    (1, 4, 1): "bull",  # 2
    (1, 4, 2): "strong bull",  # 26
    (2, -1, 0): "rally",  # 23
    (2, -1, 1): "rally",  # 5
    (2, -1, 2): "strong bull",  # 5
    (2, 0, 1): "neutral",  # 2
    (2, 1, 0): "improving",  # 4
    (2, 1, 1): "bull",  # 5
    (2, 1, 2): "strong bull",  # 2
    (2, 3, 0): "improving",  # 3
    (2, 3, 1): "bull",  # 7
    (2, 3, 2): "strong bull",  # 10
    (2, 4, 0): "improving",  # 3
    (2, 4, 1): "bull",  # 45
    (2, 4, 2): "strong bull",  # 168
    (3, -3, 0): "rally",  # 3
    (3, -2, 1): "bear",  # 1
    (3, -2, 2): "weakening",  # 1
    (3, -1, 0): "rally",  # 88
    (3, -1, 1): "rally",  # 29
    (3, -1, 2): "neutral",  # 24
    (3, 0, 2): "weakening",  # 1
    (3, 1, 0): "strong bear",  # 8
    (3, 1, 1): "bull",  # 14
    (3, 1, 2): "strong bull",  # 18
    (3, 2, 1): "pullback",  # 2
    (3, 2, 2): "strong bull",  # 10
    (3, 3, 0): "rally",  # 2
    (3, 3, 1): "bull",  # 11
    (3, 3, 2): "strong bull",  # 35
    (3, 4, 0): "improving",  # 2
    (3, 4, 1): "bull",  # 42
    (3, 4, 2): "strong bull",  # 433
    (4, -4, 0): "strong bear",  # 1
    (4, -2, 1): "rally",  # 2
    (4, -1, 0): "improving",  # 76
    (4, -1, 1): "bull",  # 20
    (4, -1, 2): "strong bull",  # 20
    (4, 0, 1): "bull",  # 3
    (4, 0, 2): "strong bull",  # 1
    (4, 1, 0): "improving",  # 17
    (4, 1, 1): "bull",  # 40
    (4, 1, 2): "strong bull",  # 40
    (4, 2, 1): "pullback",  # 6
    (4, 2, 2): "pullback",  # 5
    (4, 3, 0): "improving",  # 9
    (4, 3, 1): "bull",  # 37
    (4, 3, 2): "strong bull",  # 148
    (4, 4, 0): "improving",  # 7
    (4, 4, 1): "bull",  # 194
    (4, 4, 2): "strong bull",  # 1914
}
