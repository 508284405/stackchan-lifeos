from setuptools import find_packages, setup

setup(
    name="stackchan-brain",
    version="0.1.0",
    packages=find_packages(),
    install_requires=["langgraph>=0.6.11,<2", "pydantic>=2,<3", "fastapi>=0.116,<1"],
    entry_points={"console_scripts": ["stackchan-brain=brain.cli:main"]},
)
