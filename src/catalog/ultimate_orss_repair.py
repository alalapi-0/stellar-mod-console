"""Generate the exact Ultimate ORSS label repair; never install it into a game."""

import argparse
import hashlib
import json
from pathlib import Path


SOURCE_SHA256 = "840b767208397b987dc774492b7c4f65cc846bf9efc551650c1a72898f583ba3"
REPAIRED_SHA256 = "6eca2812f760810e2926e9e89790ce819e5d01dbc36ff3be6b2dd0bdb4f64248"


def repair(source: bytes) -> bytes:
    digest = hashlib.sha256(source).hexdigest()
    if digest == REPAIRED_SHA256:
        return source
    if digest != SOURCE_SHA256:
        raise ValueError("Unknown preimage; this repair supports one exact declaration")
    document = json.loads(source)
    textures = document[0]["UserConfigs"]["TextureOptions"]
    reference, target = textures[4], textures[5]
    labels = reference["OptionNames"]
    if (document[0]["UniqueFitID"] != "UltimateNudeEve"
            or reference["DisplayName"] != "Body Normal"
            or target["DisplayName"] != "Body ORSS"
            or target["ControlledBy"] != "Body Normal"
            or len(labels) != 6 or len(target["Textures"]) != 6
            or labels[2] != "More Pubic hair"
            or target["OptionNames"] != labels[:2] + labels[3:]):
        raise ValueError("The paired author declarations no longer match the repair")
    marker = b'"DisplayName": "Body ORSS"'
    if source.count(marker) != 1:
        raise ValueError("Ambiguous target declaration")
    start = source.index(marker)
    end = source.index(b'"Textures": [', start)
    needle = b'            "Big Areola",\n'
    section = source[start:end]
    if section.count(needle) != 1:
        raise ValueError("Unexpected label layout")
    offset = start + section.index(needle)
    result = source[:offset] + b'            "More Pubic hair",\n' + source[offset:]
    target["OptionNames"] = list(labels)
    if json.loads(result) != document or hashlib.sha256(result).hexdigest() != REPAIRED_SHA256:
        raise ValueError("Candidate differs from the exact reviewed repair")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path, help="New candidate file; existing files are refused")
    args = parser.parse_args(argv)
    before = args.source.read_bytes()
    after = repair(before)
    with args.output.open("xb") as output:
        output.write(after)
    print(json.dumps({"before_sha256": hashlib.sha256(before).hexdigest(),
                      "after_sha256": hashlib.sha256(after).hexdigest(),
                      "added_bytes": len(after) - len(before), "installed": False}))


if __name__ == "__main__":
    main()
