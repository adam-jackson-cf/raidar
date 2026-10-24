import subprocess


def test_multiplies(entrypoint_argv):
    result = subprocess.run([*entrypoint_argv, "mul", "2", "3"], capture_output=True, text=True)
    assert result.stdout.strip() == "6"
