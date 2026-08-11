---------------------------- MODULE AuthorityContract ----------------------------
(***************************************************************************)
(* Algorithm-independent Authority contract for Kawa (issue #38).          *)
(*                                                                         *)
(* This is the CONTRACT, not a consensus algorithm: it models             *)
(* configurations, proven succession, and quorum-authorized acceptance as *)
(* abstract state, and asserts the safety invariants S1-S8 that any        *)
(* concrete profile (Raft joint-consensus, HotStuff, lattice/dominance    *)
(* reconfiguration, heterogeneous quorums, ...) MUST satisfy.             *)
(*                                                                         *)
(* The runnable stdlib checker formal/check_authority.py explores the same *)
(* state machine (TLC needs a JVM this environment lacks) and empirically  *)
(* verifies S1/S2 plus a negative control. Both encode the same finding:   *)
(* quorum overlap with the PARENT is insufficient; the contract requires   *)
(* UNIQUE AUTHORITATIVE SUCCESSION (a config is superseded by at most one  *)
(* successor), which sharpens #38 S3 into an enforced precondition.        *)
(***************************************************************************)
EXTENDS FiniteSets, Naturals

CONSTANTS Nodes,          \* set of node ids
          Quorum,         \* quorum size (profile: 2f+1)
          Ops             \* set of operation ids for the key; distinct ops conflict

VARIABLES legit,          \* set of legitimate configuration ids
          superseded,     \* set of configuration ids that have a successor
          accepted,       \* set of <<config, op>> accepted with a quorum proof
          distrusted      \* set of distrusted nodes

vars == <<legit, superseded, accepted, distrusted>>

(* A fixed candidate configuration pool: C0 (Genesis) and two incomparable  *)
(* successors C1, C2, each quorum-overlapping C0 but not each other.        *)
Members(c) == CASE c = "C0" -> Nodes
                [] c = "C1" -> Nodes \ {3}
                [] c = "C2" -> Nodes \ {0}
Parent(c)  == CASE c = "C0" -> "GENESIS"
                [] c = "C1" -> "C0"
                [] c = "C2" -> "C0"
Configs    == {"C0", "C1", "C2"}

QuorumOf(c)      == Cardinality(Members(c) \ distrusted) >= Quorum
OverlapQuorum(a,b) == Cardinality(Members(a) \cap Members(b)) >= Quorum

(* Comparable: one dominates the other along the parent chain. *)
Comparable(c1, c2) == \/ c1 = c2
                      \/ Parent(c1) = c2
                      \/ Parent(c2) = c1

Init == /\ legit = {"C0"}
        /\ superseded = {}
        /\ accepted = {}
        /\ distrusted = {}

(* Reconfigure: a legit, not-yet-superseded parent's quorum authorizes exactly *)
(* one successor that shares a quorum of members. (Unique authoritative        *)
(* succession is the "at most one successor" guard: Parent(c) \notin superseded)*)
Reconfigure(c) ==
    /\ c \in Configs /\ c \notin legit
    /\ Parent(c) \in legit
    /\ Parent(c) \notin superseded
    /\ QuorumOf(Parent(c))
    /\ OverlapQuorum(Parent(c), c)
    /\ legit' = legit \cup {c}
    /\ superseded' = superseded \cup {Parent(c)}
    /\ UNCHANGED <<accepted, distrusted>>

(* Accept: a legit, not-superseded config with a quorum proof accepts an op. *)
Accept(c, op) ==
    /\ c \in legit /\ c \notin superseded
    /\ op \in Ops
    /\ QuorumOf(c)
    /\ <<c, op>> \notin accepted
    \* NON-EQUIVOCATION (fix F1): one configuration is a single decision basis for the key;
    \* it may authorize at most one op. It cannot also accept a different, conflicting op.
    /\ \A p \in accepted : p[1] = c => p[2] = op
    /\ accepted' = accepted \cup {<<c, op>>}
    /\ UNCHANGED <<legit, superseded, distrusted>>

Distrust(n) ==
    /\ n \in Nodes /\ n \notin distrusted
    /\ distrusted' = distrusted \cup {n}
    /\ UNCHANGED <<legit, superseded, accepted>>

Next == \/ \E c \in Configs : Reconfigure(c)
        \/ \E c \in Configs, op \in Ops : Accept(c, op)
        \/ \E n \in Nodes : Distrust(n)

Spec == Init /\ [][Next]_vars

(* ---- Safety invariants (S1, S2/S4) ---- *)

(* S2/S4: every accepted op sits under a legitimate configuration. *)
AuthorityOnlyUnderLegit ==
    \A pair \in accepted : pair[1] \in legit

(* S1 + S3: two conflicting ops for the key are permitted only under DIFFERENT, comparable
   configurations (a legitimate supersession). Same-config conflict (equivocation, p1[1]=p2[1])
   and incomparable-config conflict (split-brain) are both violations. (fix F1) *)
NoConflictingAuthority ==
    \A p1, p2 \in accepted :
        (p1[2] # p2[2]) => (p1[1] # p2[1] /\ Comparable(p1[1], p2[1]))

Safety == AuthorityOnlyUnderLegit /\ NoConflictingAuthority
=============================================================================
