from pathlib import Path

from setuptools import find_packages, setup


README = Path(__file__).with_name("README.md").read_text(encoding="utf-8")


setup(
    name="spanish-cloze-practice",
    version="0.1.0",
    description="Terminal-first Spanish cloze practice with spaced repetition and local Tatoeba sentence data.",
    long_description=README,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    include_package_data=True,
    package_data={"spanish_vocab": ["vocab/*.txt"]},
    entry_points={"console_scripts": ["spanish=spanish_vocab.__main__:main"]},
    python_requires=">=3.6",
)
