"""Published native workflow configurations; readiness is checked separately."""

import platform

VALIDATED_BUILDS = [
    {
        "platform": "macOS arm64",
        "system": "Darwin",
        "machine": "arm64",
        "openvsp_version": "3.53.1",
        "vspaero_version": "7.2.2",
        "openvsp_sha256": "856bf05b14f9ecf56581b071fe0f801165286d22ab0b0f74b30b7f36889969d8",
        "vspaero_sha256": "7ddca47bf20fecf6db416f8ed531b14afc277927f133fa0995a5c87ba058f169",
    },
    {
        "platform": "Ubuntu 24.04 x86_64",
        "system": "Linux",
        "machine": "x86_64",
        "distribution_id": "ubuntu",
        "distribution_version": "24.04",
        "openvsp_version": "3.53.1",
        "vspaero_version": "7.2.2",
        "openvsp_sha256": "f86b1a785eca04d703d0fbd573f6e55d356e5cbb728d693190cd2079b4d92b12",
        "vspaero_sha256": "b8d62390a5542e3f9ff20d5accd4fd3e01efc21dba6fab945f2b780cb0738d4a",
    },
]


def platform_info():
    result = {"system": platform.system(), "machine": platform.machine()}
    if result["system"] == "Linux":
        try:
            release = platform.freedesktop_os_release()
        except OSError:
            release = {}
        result.update(
            distribution_id=release.get("ID"), distribution_version=release.get("VERSION_ID")
        )
    return result


def validation_metadata(checks, identities):
    current = platform_info()
    matched = any(
        all(current.get(key) == build[key] for key in current)
        and all(
            checks.get(name, {}).get("status") == "ok"
            and checks[name].get("version") == build[name + "_version"]
            and identities.get(name, {}).get("sha256") == build[name + "_sha256"]
            for name in ("openvsp", "vspaero")
        )
        for build in VALIDATED_BUILDS
    )
    return {
        "runtime_platform": current,
        "tested_native_build": matched,
        "validation_scope": {
            "platform": "; ".join(build["platform"] for build in VALIDATED_BUILDS),
            "version_pairs": [["3.53.1", "7.2.2"]],
            "configurations": VALIDATED_BUILDS,
            "meaning": (
                "Published native workflow evidence. tested_version_pair checks version strings "
                "only; tested_native_build also requires this platform and both binary hashes. "
                "Health itself does not run these workflows."
            ),
        },
    }
