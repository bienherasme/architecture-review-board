"""Reproducible behavioral evaluation harness for Architecture Review Board.

This package answers measurable questions about board behavior against a
fixed, versioned, synthetic golden dataset: did the workflow complete,
did all five specialist dimensions execute, did expected material risks
appear in the correct dimension, was the final decision within a
human-authored acceptable set, were expected disagreements represented,
was cited evidence valid.

It deliberately does not, and cannot, measure universal architecture
quality, intelligence, or business value, and it never produces a single
weighted composite score. There is no LLM-as-judge here: every assertion
is a deterministic, offline, lexically-anchored check over the same
public ArchitectureReviewResult any caller would see.
"""
