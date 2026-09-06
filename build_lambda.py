import os
import subprocess
import shutil

print("Installing Linux-compatible dependencies for AWS Lambda (Python 3.12)...")
if os.path.exists("package"):
    shutil.rmtree("package")
subprocess.run([
    "pip", "install",
    "--platform", "manylinux2014_x86_64",
    "--target", "package",
    "--implementation", "cp",
    "--python-version", "3.12",
    "--only-binary=:all:",
    "-r", "backend/requirements.txt"
])

print("Copying application code...")
shutil.copytree("backend", "package/backend", dirs_exist_ok=True)
shutil.copytree("rca_engine", "package/rca_engine", dirs_exist_ok=True)
shutil.copytree("event_processor", "package/event_processor", dirs_exist_ok=True)
shutil.copy2("aws_lambda/lambda_handler.py", "package/lambda_handler.py")

print("Zipping package...")
shutil.make_archive("deployment", "zip", "package")

print("Deployment package created successfully: deployment.zip")
