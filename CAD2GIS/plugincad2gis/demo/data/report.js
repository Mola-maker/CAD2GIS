window.REPORT = {
  "source": "DS-04_comms.dxf",
  "track": "Sub-track 2 · 多源异构工程数据融合",
  "source_entities": 120598,
  "total_features": 3089,
  "counts": {
    "duct": 698,
    "manhole": 258,
    "annotation": 1044,
    "cable": 1089
  },
  "raw_breakdown": [
    [
      "LINE",
      44679
    ],
    [
      "LWPOLYLINE",
      32392
    ],
    [
      "INSERT",
      22329
    ],
    [
      "TEXT",
      7354
    ],
    [
      "SOLID",
      5176
    ],
    [
      "CIRCLE",
      3591
    ]
  ],
  "accuracy": {
    "overall": 0.9737,
    "passed": true,
    "threshold": 0.9,
    "dimensions": [
      {
        "name": "semantic",
        "score": 0.9948,
        "evaluated": true,
        "details": "coverage=0.99 (in-vocabulary rate; per-feature correctness NOT independently verified — no labeled ground truth) abstained=1655"
      },
      {
        "name": "geometric",
        "score": 0.9987,
        "evaluated": true,
        "details": "valid=4754/4760"
      },
      {
        "name": "count",
        "score": 0.9961,
        "evaluated": true,
        "details": "expected={'manhole': 259} actual={'manhole': 258} abs_err=1"
      },
      {
        "name": "attribute",
        "score": 1.0,
        "evaluated": true,
        "details": "filled=8223/8223"
      },
      {
        "name": "network",
        "score": 0.8535,
        "evaluated": true,
        "details": "dangling=382 isolated=19"
      },
      {
        "name": "positional",
        "score": 0.6728,
        "evaluated": true,
        "details": "rmse=1.96m target=3.0m n=247"
      }
    ]
  },
  "network": {
    "n_nodes": 662,
    "n_edges": 1304,
    "dangling_ends": 382,
    "isolated_nodes": 19,
    "connected_endpoints": 2226,
    "total_endpoints": 2608,
    "connectivity_ratio": 0.8535
  },
  "georef": {
    "model": "similarity",
    "params": [
      0.9999329726788565,
      -0.0001737111920183665,
      8.323305156822515,
      11.527396393409804
    ],
    "n_gcps": 247,
    "rmse": 1.9633,
    "max_error": 7.6945,
    "src_crs": "local-engineering",
    "dst_crs": "local-engineering-grid (EPSG unknown; X=northing,Y=easting; fitted transform)",
    "outliers": [
      55,
      121,
      195,
      198,
      232,
      233,
      247
    ]
  },
  "refine": {
    "fragments_demoted": 1655,
    "routes_kept": 1304,
    "endpoints_snapped": 689,
    "junctions_found": 1301,
    "details": [],
    "propagated": {
      "upgraded": 320,
      "checked": 327
    }
  },
  "attributes_added": 620,
  "benchmark_note": "Ground truth: manhole count anchored to 259 surveyed X=/Y= node labels (an independent entity type). Per-feature correctness measured via cross-source signals INDEPENDENT of the classifier's rule path (manhole↔surveyed-label match, cable↔topological anchoring, duct↔geometry fingerprint, annotation↔text). Positional from GCP RMSE. Dimensions without an independent source are shown but marked not-scored — never faked.",
  "per_feature": {
    "by_class": {
      "manhole": {
        "total": 258,
        "verified": 253,
        "verifiable": 258,
        "rate": 0.9806
      },
      "cable": {
        "total": 1089,
        "verified": 1062,
        "verifiable": 1089,
        "rate": 0.9752
      },
      "duct": {
        "total": 698,
        "verified": 193,
        "verifiable": 698,
        "rate": 0.2765
      },
      "annotation": {
        "total": 1044,
        "verified": 1044,
        "verifiable": 1044,
        "rate": 1.0
      }
    },
    "overall_verified": 2552,
    "overall_verifiable": 3089,
    "per_feature_correctness": 0.8261573324700551
  },
  "evidence": [
    {
      "block": "GC200",
      "count": 9690,
      "reason": "no rule and no reviewed block-code",
      "nearest_text_top": [
        [
          "砖",
          622
        ],
        [
          "坝",
          275
        ],
        [
          "空",
          246
        ],
        [
          "地砖",
          164
        ],
        [
          "3孔PVC110",
          132
        ]
      ]
    },
    {
      "block": "gc119",
      "count": 1252,
      "reason": "no rule and no reviewed block-code",
      "nearest_text_top": [
        [
          "砖",
          16
        ],
        [
          "水泥",
          5
        ],
        [
          "断",
          4
        ],
        [
          "坝",
          3
        ],
        [
          "空",
          3
        ]
      ]
    },
    {
      "block": "7023",
      "count": 918,
      "reason": "no rule and no reviewed block-code",
      "nearest_text_top": [
        [
          "380.0",
          7
        ],
        [
          "坝",
          6
        ],
        [
          "390.0",
          5
        ],
        [
          "385.0",
          4
        ],
        [
          "土",
          4
        ]
      ]
    },
    {
      "block": "7006",
      "count": 835,
      "reason": "no rule and no reviewed block-code",
      "nearest_text_top": [
        [
          "375.0",
          11
        ],
        [
          "377.5",
          10
        ],
        [
          "石",
          10
        ],
        [
          "水",
          8
        ],
        [
          "380.0",
          5
        ]
      ]
    },
    {
      "block": "gc170",
      "count": 578,
      "reason": "evidence gate failed (needs 孔|PVC|BD|管)",
      "nearest_text_top": [
        [
          "水泥",
          20
        ],
        [
          "坝",
          12
        ],
        [
          "砖",
          7
        ],
        [
          "363.73",
          4
        ],
        [
          "X=-19372.28",
          4
        ]
      ]
    },
    {
      "block": "gc043",
      "count": 513,
      "reason": "reviewed non-comms: 513x; nearest 地砖(35)/水泥/砖 -> paving, non-comms",
      "nearest_text_top": [
        [
          "地砖",
          35
        ],
        [
          "3孔PVC110",
          9
        ],
        [
          "水泥",
          7
        ],
        [
          "X=-19302.24",
          4
        ],
        [
          "351.78",
          4
        ]
      ]
    },
    {
      "block": "8110",
      "count": 513,
      "reason": "no rule and no reviewed block-code",
      "nearest_text_top": [
        [
          "坝",
          3
        ],
        [
          "386.50",
          1
        ],
        [
          "386.27",
          1
        ],
        [
          "385.77",
          1
        ],
        [
          "384.67",
          1
        ]
      ]
    },
    {
      "block": "gc124",
      "count": 496,
      "reason": "reviewed non-comms: 496x on ZBTZ; fp {LINE:4}; nearest 砖(24)/坝/水泥/地砖/砼 -> paving, non-comms",
      "nearest_text_top": [
        [
          "砖",
          24
        ],
        [
          "坝",
          15
        ],
        [
          "水泥",
          14
        ],
        [
          "地砖",
          12
        ],
        [
          "砼",
          9
        ]
      ]
    }
  ]
};
