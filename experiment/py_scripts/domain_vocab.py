#!/usr/bin/env python3
"""
Domain vocabulary loader and validator for FTTH GIS data.

Loads domain values from 14 CSV domain dictionaries in ../Shape/ and
provides case-insensitive validation functions for all FTTH feature classes.
"""

import csv
import json
import os
import sys
import warnings

SHAPE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'official', 'Shape')

CSV_MAP = {
    'l_statut.csv': 'STATUT',
    'l_cable_type.csv': 'TYPE_CABLE',
    'l_fibre_type.csv': 'TYPE_FIBRE',
    'l_mode_pose.csv': 'MODE_POSE',
    'l_ptc_type.csv': 'PTECH_TYPE',
    'l_ptc_nature.csv': 'PTECH_NATURE',
    'l_site_type.csv': 'SITE_TYPE',
    'l_type_log.csv': 'TYPE_LOG',
    'l_type_prop.csv': 'TYPE_PROP',
    'l_imb_type.csv': 'IMB_TYPE',
    'l_imb_bat.csv': 'IMB_BATIMENT',
    'l_imb_racco.csv': 'IMB_RACCORDEMENT',
    'l_bpe_mode_pose.csv': 'BPE_MODE_POSE',
    'Type boite.csv': 'BOITE_TYPE',
}

MODE_POSE_BOITE_VALUES = ['FACADE', 'CHAMBRE', 'AERIEN']

HARDCODED_FALLBACK = {
    'STATUT': ['DEPLOYE', 'EN COURS DE DEPLOIEMENT', 'EN PROJET'],
    'TYPE_CABLE': ['TRANSPORT', 'DISTRIBUTION', 'RACCORDEMENT', 'VERTICALITE', 'COLLECTE'],
    'TYPE_FIBRE': ['G652', 'G652A', 'G652B', 'G652C', 'G652D',
                   'G657', 'G657A', 'G657A1', 'G657A2', 'G657A3',
                   'G657B', 'G657B1', 'G657B2', 'G657B3'],
    'MODE_POSE': ['SOUTERRAIN', 'AERIEN', 'FACADE', 'IMMEUBLE', 'COLONNE MONTANTE'],
    'MODE_POSE_BOITE': ['FACADE', 'CHAMBRE', 'AERIEN'],
    'BOITE_TYPE': ['BPE', 'PBO', 'BPI', 'PTO'],
    'SITE_TYPE': ['NRO', 'PM', 'ARMOIRE DE RUE', 'BATIMENT', 'LOCAL TECHNIQUE', 'SHELTER'],
    'PTECH_TYPE': ['APPUI', 'CHAMBRE', 'ANCRAGE FACADE', 'IMMEUBLE', 'AUTRE'],
    'PTECH_NATURE': [
        'A1', 'A2', 'A3', 'A4', 'A10', 'A11', 'A12', 'A13', 'A14', 'A15', 'A16', 'A17', 'A18',
        'B1', 'B2', 'B3', 'B4',
        'C1', 'C2', 'C3', 'C4',
        'D1', 'D1C', 'D1T', 'D2', 'D2C', 'D2T', 'D3', 'D3C', 'D3T',
        'D4', 'D4C', 'D4T', 'D5', 'D5C', 'D6', 'D6C', 'D11', 'D12', 'D13', 'D14',
        'E1', 'E2', 'E3', 'E4',
        'J2C', 'J2CR', 'K1C', 'K1CR', 'K1T', 'K2C', 'K2CR', 'K2T',
        'K3C', 'K3CR', 'K3T', 'L0T', 'L0TR', 'L1C', 'L1T', 'L1TR',
        'L2C', 'L2T', 'L2TR', 'L3C', 'L3T', 'L3TR', 'L4C', 'L4T', 'L4TR',
        'L5C', 'L5T', 'L5TR', 'L6T', 'L6TR', 'M1C', 'M1CR', 'M2T', 'M2TR', 'M3C', 'M3CR',
        'P1C', 'P1CR', 'P1T', 'P1TR', 'P2C', 'P2CR', 'P2T', 'P2TR',
        'P3C', 'P3T', 'P4C', 'P4T', 'P5C', 'P5T', 'P6C', 'P6T',
        'R1T', 'R2T', 'R3T',
        'OHN', 'PBOI', 'PBET', 'PCMP', 'PMET', 'PIND', 'POTL',
        'REG', 'R40', 'BAL', 'CRO', 'FAI', 'STR', 'SSO', 'TRA', 'IND', 'PNS3', 'AUTRE',
    ],
    'TYPE_LOG': ['TRANSPORT', 'DISTRIBUTION', 'RACCORDEMENT'],
    'TYPE_PROP': ['CESSION', 'CONSTRUCTION', 'IRU', 'LOCATION', 'OCCUPATION'],
    'IMB_TYPE': ['RESIDENTIEL', 'PROFESSIONNEL', 'ADMINISTRATION', 'ENTREPRISE', 'OPERATEUR'],
    'IMB_BATIMENT': [
        'BATIMENT PUBLIC', 'BATIMENT RELIGIEUX', 'COMMERCE', 'DIVERS', 'ENTREPOT',
        'ENTREPRISE', 'EOLIENNE', 'EQUIPEMENT SPORTIF', 'ETABLISSEMENT PRIVE',
        'EXPLOITATION AGRICOLE', 'IMMEUBLE', 'BATIMENT', 'VILLA',
        'BATIMENT R+1', 'BATIMENT R+2', 'BATIMENT R+3', 'IMMEUBLE COLLECTIF',
        'POSTE ELECTRIQUE', 'PYLONE', 'STATION METEO', 'STATION POMPAGE', 'USINE',
    ],
    'IMB_RACCORDEMENT': ['SOUTERRAIN', 'FACADE', 'AERIEN', 'COLONNE MONTANTE'],
    'BPE_MODE_POSE': ['FACADE', 'CHAMBRE', 'AERIEN'],
}

LAYER_DOMAIN_MAP = {
    'BOITE': {
        'TYPE': 'BOITE_TYPE',
        'MODE_POSE': 'MODE_POSE_BOITE',
        'STATUT': 'STATUT',
    },
    'CABLE': {
        'TYPE_CABLE': 'TYPE_CABLE',
        'TYPE_FIBRE': 'TYPE_FIBRE',
        'MODE_POSE': 'MODE_POSE',
        'STATUT': 'STATUT',
        'TYPE_PROP': 'TYPE_PROP',
    },
    'PTECH': {
        'TYPE': 'PTECH_TYPE',
        'NATURE': 'PTECH_NATURE',
        'STATUT': 'STATUT',
    },
    'SITE': {
        'TYPE': 'SITE_TYPE',
        'STATUT': 'STATUT',
    },
    'INFRASTRUCTURE': {
        'TYPE_LOG': 'TYPE_LOG',
        'STATUT': 'STATUT',
    },
    'IMB': {
        'TYPE_BATIMENT': 'IMB_BATIMENT',
        'TYPE_CLIENT': 'IMB_TYPE',
        'RACCORDEMENT': 'IMB_RACCORDEMENT',
        'STATUT': 'STATUT',
    },
    'ZNRO': {
        'STATUT': 'STATUT',
    },
    'ZPM': {
        'STATUT': 'STATUT',
    },
}


def _parse_csv(filepath):
    """Parse a semicolon-separated CSV domain file. Returns list of unique UPPERCASE values."""
    values = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Format: "VALUE;VALUE" — take the first semicolon-delimited field
                val = line.split(';')[0].strip().upper()
                if val:
                    values.append(val)
    except Exception as e:
        warnings.warn(f"Failed to parse {filepath}: {e}")
        return []
    # Deduplicate preserving order
    seen = set()
    unique = []
    for v in values:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    return unique


def load_domains(shape_dir=None):
    """
    Load all domain vocabularies from CSV files.

    Returns a dict mapping domain key -> list of valid UPPERCASE values.
    Falls back to HARDCODED_FALLBACK for missing or unparseable files.
    """
    if shape_dir is None:
        shape_dir = SHAPE_DIR

    domains = {}
    for filename, key in CSV_MAP.items():
        filepath = os.path.join(shape_dir, filename)
        if os.path.isfile(filepath):
            values = _parse_csv(filepath)
            if values:
                domains[key] = values
                continue
        warnings.warn(f"Using hardcoded fallback for {key} (CSV missing or unparseable)")
        domains[key] = list(HARDCODED_FALLBACK.get(key, []))

    for key in ('MODE_POSE_BOITE',):
        if key not in domains:
            domains[key] = list(MODE_POSE_BOITE_VALUES)

    # Ensure every key from HARDCODED_FALLBACK that doesn't have a CSV entry
    # is still present (e.g., MODE_POSE_BOITE)
    for key in HARDCODED_FALLBACK:
        if key not in domains:
            domains[key] = list(HARDCODED_FALLBACK[key])

    return domains


# Module-level cache
_domains_cache = None


def _get_domains():
    global _domains_cache
    if _domains_cache is None:
        _domains_cache = load_domains()
    return _domains_cache


def _make_lookup(domain_key):
    """Build a set of UPPERCASE valid values for fast lookup."""
    domains = _get_domains()
    return set(domains.get(domain_key, []))


# ─── Validator Functions ────────────────────────────────────────────

def validate_statut(value):
    """Check if value is a valid STATUT domain entry (case-insensitive)."""
    if value is None:
        return False
    return value.strip().upper() in _make_lookup('STATUT')


def validate_type_cable(value):
    """Check if value is a valid TYPE_CABLE domain entry (case-insensitive)."""
    if value is None:
        return False
    return value.strip().upper() in _make_lookup('TYPE_CABLE')


def validate_type_fibre(value):
    """Check if value is a valid TYPE_FIBRE domain entry (case-insensitive)."""
    if value is None:
        return False
    return value.strip().upper() in _make_lookup('TYPE_FIBRE')


def validate_mode_pose(fc_name, value):
    """
    Check if value is a valid MODE_POSE for the given feature class.

    Handles per-FC differences:
      - BOITE uses MODE_POSE_BOITE (3 values: FACADE, CHAMBRE, AERIEN)
      - CABLE uses MODE_POSE (5 values)
      - Other FCs fall back to MODE_POSE
    """
    if value is None:
        return False
    v = value.strip().upper()
    fc_upper = fc_name.strip().upper()
    if fc_upper == 'BOITE':
        return v in _make_lookup('MODE_POSE_BOITE')
    return v in _make_lookup('MODE_POSE')


def validate_boite_type(value):
    """Check if value is a valid BOITE_TYPE domain entry (case-insensitive)."""
    if value is None:
        return False
    return value.strip().upper() in _make_lookup('BOITE_TYPE')


def validate_site_type(value):
    """Check if value is a valid SITE_TYPE domain entry (case-insensitive)."""
    if value is None:
        return False
    return value.strip().upper() in _make_lookup('SITE_TYPE')


def validate_ptech_type(value):
    """Check if value is a valid PTECH_TYPE domain entry (case-insensitive)."""
    if value is None:
        return False
    return value.strip().upper() in _make_lookup('PTECH_TYPE')


def validate_imb_batiment(value):
    """Check if value is a valid IMB_BATIMENT domain entry (case-insensitive)."""
    if value is None:
        return False
    return value.strip().upper() in _make_lookup('IMB_BATIMENT')


def validate_domain_value(fc_name, field_name, value):
    """
    Validate a value against the domain vocabulary for a given FC and field.

    Returns (is_valid: bool, valid_values: list).
    """
    if value is None:
        fc_upper = fc_name.strip().upper()
        field_upper = field_name.strip().upper()
        mapping = LAYER_DOMAIN_MAP.get(fc_upper, {})
        domain_key = mapping.get(field_upper)
        if domain_key:
            return False, list(_get_domains().get(domain_key, []))
        return False, []

    v = value.strip().upper()
    fc_upper = fc_name.strip().upper()
    field_upper = field_name.strip().upper()

    mapping = LAYER_DOMAIN_MAP.get(fc_upper, {})
    domain_key = mapping.get(field_upper)

    if domain_key is None:
        return False, []

    domains = _get_domains()
    valid_values = list(domains.get(domain_key, []))
    is_valid = v in set(valid_values)
    return is_valid, valid_values


# ─── JSON Export / Import ───────────────────────────────────────────

def save_domain_vocab(output_path):
    """
    Write the merged domain vocabulary dict to a JSON file.

    Returns the dict written.
    """
    domains = _get_domains()
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(domains, f, ensure_ascii=False, indent=2)
    return domains


def load_domain_vocab(json_path):
    """
    Load a domain vocabulary dict from a JSON file.

    Returns the dict.
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ─── CLI ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Domain vocabulary loader for FTTH GIS')
    parser.add_argument('--output', '-o', default=None,
                        help='Write merged domain_vocab.json to the given path')
    parser.add_argument('--list', '-l', action='store_true',
                        help='List all domain keys with value counts')
    parser.add_argument('--dump', '-d', default=None,
                        help='Dump values for a specific domain key')
    args = parser.parse_args()

    domains = load_domains()

    if args.list:
        for key in sorted(domains.keys()):
            print(f"{key}: {len(domains[key])} values")
    elif args.dump:
        key = args.dump.upper()
        if key in domains:
            for v in domains[key]:
                print(v)
        else:
            print(f"Unknown domain key: {key}", file=sys.stderr)
            print(f"Available: {', '.join(sorted(domains.keys()))}", file=sys.stderr)
            sys.exit(1)
    elif args.output:
        save_domain_vocab(args.output)
        print(f"Wrote domain vocabulary to {args.output}")
    else:
        print(json.dumps(domains, ensure_ascii=False, indent=2))
