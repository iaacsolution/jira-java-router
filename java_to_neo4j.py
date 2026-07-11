"""
Analyse Java → Neo4j (Windows)
================================
Parse les fichiers .java et importe le graphe dans Neo4j :
  - Nœuds  : Class, Interface, Method, Package
  - Relations : EXTENDS, IMPLEMENTS, CALLS, HAS_METHOD, IN_PACKAGE, DEPENDS_ON

Prérequis :
    docker run -d --name neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/Admin2024! neo4j:latest
    pip install neo4j javalang

Usage :
    python java_to_neo4j.py --src C:\\chemin\\vers\\projet
    python java_to_neo4j.py --src C:\\Users\\trist\\stephane\\projets\\java-legacy-agent\\agent\\src
"""

import argparse
import os
from pathlib import Path

import javalang
from neo4j import GraphDatabase

# ── Config Neo4j ─────────────────────────────────────────────────────────────
NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "Admin2024!")


# ── Parser Java ───────────────────────────────────────────────────────────────

def parse_java_file(path: Path) -> dict | None:
    """Parse un fichier .java et retourne un dict avec classes, méthodes, dépendances."""
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree   = javalang.parse.parse(source)
    except Exception as e:
        print(f"  [Parse] Erreur {path.name} : {e}")
        return None

    result = {
        "file":       str(path),
        "package":    None,
        "classes":    [],
        "interfaces": [],
    }

    # Package
    if tree.package:
        result["package"] = tree.package.name

    for _, node in tree:

        # Classe
        if isinstance(node, javalang.tree.ClassDeclaration):
            cls = {
                "name":       node.name,
                "extends":    node.extends.name if node.extends else None,
                "implements": [i.name for i in (node.implements or [])],
                "methods":    [],
                "imports":    [imp.path for imp in tree.imports],
            }

            # Méthodes
            for method in node.methods:
                params = [p.type.name for p in method.parameters]
                calls  = []

                # Appels de méthodes dans le corps
                if method.body:
                    for _, mnode in method.filter(javalang.tree.MethodInvocation):
                        calls.append(mnode.member)

                cls["methods"].append({
                    "name":       method.name,
                    "return":     method.return_type.name if method.return_type else "void",
                    "params":     params,
                    "calls":      list(set(calls)),
                    "visibility": _visibility(method.modifiers),
                })

            result["classes"].append(cls)

        # Interface
        elif isinstance(node, javalang.tree.InterfaceDeclaration):
            result["interfaces"].append({
                "name":    node.name,
                "extends": [e.name for e in (node.extends or [])],
            })

    return result


def _visibility(modifiers) -> str:
    if not modifiers:
        return "package"
    for m in ["public", "protected", "private"]:
        if m in modifiers:
            return m
    return "package"


# ── Import Neo4j ──────────────────────────────────────────────────────────────

def import_to_neo4j(driver, parsed: dict) -> None:
    """Importe un fichier parsé dans Neo4j."""
    pkg = parsed["package"] or "default"

    with driver.session() as s:

        # Package
        s.run("MERGE (p:Package {name: $name})", name=pkg)

        # Classes
        for cls in parsed["classes"]:
            fqn = f"{pkg}.{cls['name']}"

            s.run("""
                MERGE (c:Class {name: $name, fqn: $fqn})
                SET c.file = $file
            """, name=cls["name"], fqn=fqn, file=parsed["file"])

            # Lien classe → package
            s.run("""
                MATCH (c:Class {fqn: $fqn})
                MATCH (p:Package {name: $pkg})
                MERGE (c)-[:IN_PACKAGE]->(p)
            """, fqn=fqn, pkg=pkg)

            # EXTENDS
            if cls["extends"]:
                s.run("""
                    MERGE (parent:Class {name: $parent})
                    WITH parent
                    MATCH (c:Class {fqn: $fqn})
                    MERGE (c)-[:EXTENDS]->(parent)
                """, parent=cls["extends"], fqn=fqn)

            # IMPLEMENTS
            for iface in cls["implements"]:
                s.run("""
                    MERGE (i:Interface {name: $iface})
                    WITH i
                    MATCH (c:Class {fqn: $fqn})
                    MERGE (c)-[:IMPLEMENTS]->(i)
                """, iface=iface, fqn=fqn)

            # DEPENDS_ON (imports)
            for imp in cls["imports"]:
                dep_name = imp.split(".")[-1]
                s.run("""
                    MERGE (d:Class {name: $dep})
                    WITH d
                    MATCH (c:Class {fqn: $fqn})
                    MERGE (c)-[:DEPENDS_ON {via: $imp}]->(d)
                """, dep=dep_name, fqn=fqn, imp=imp)

            # Méthodes
            for method in cls["methods"]:
                method_id = f"{fqn}.{method['name']}"
                s.run("""
                    MERGE (m:Method {id: $id})
                    SET m.name = $name,
                        m.return_type = $ret,
                        m.visibility = $vis
                    WITH m
                    MATCH (c:Class {fqn: $fqn})
                    MERGE (c)-[:HAS_METHOD]->(m)
                """, id=method_id, name=method["name"],
                     ret=method["return"], vis=method["visibility"], fqn=fqn)

                # CALLS
                for call in method["calls"]:
                    s.run("""
                        MERGE (target:Method {name: $call})
                        WITH target
                        MATCH (m:Method {id: $id})
                        MERGE (m)-[:CALLS]->(target)
                    """, call=call, id=method_id)

        # Interfaces
        for iface in parsed["interfaces"]:
            s.run("MERGE (i:Interface {name: $name})", name=iface["name"])
            for ext in iface["extends"]:
                s.run("""
                    MERGE (parent:Interface {name: $parent})
                    WITH parent
                    MATCH (i:Interface {name: $name})
                    MERGE (i)-[:EXTENDS]->(parent)
                """, parent=ext, name=iface["name"])


def clear_db(driver) -> None:
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
    print("Base Neo4j vidée.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src",   required=True, help="Dossier source Java")
    parser.add_argument("--clear", action="store_true", help="Vider la base avant import")
    args = parser.parse_args()

    src = Path(args.src)
    if not src.exists():
        print(f"Dossier introuvable : {src}")
        return

    java_files = list(src.rglob("*.java"))
    print(f"Fichiers Java trouvés : {len(java_files)}")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    if args.clear:
        clear_db(driver)

    ok = 0
    for f in java_files:
        print(f"  → {f.name}")
        parsed = parse_java_file(f)
        if parsed:
            import_to_neo4j(driver, parsed)
            ok += 1

    driver.close()
    print(f"\n✅ {ok}/{len(java_files)} fichiers importés dans Neo4j")
    print(f"Visualisez sur http://localhost:7474")
    print(f"\nRequêtes Cypher utiles :")
    print("  MATCH (c:Class)-[:EXTENDS]->(p) RETURN c,p")
    print("  MATCH (c:Class)-[:IMPLEMENTS]->(i:Interface) RETURN c,i")
    print("  MATCH (m:Method)-[:CALLS]->(t) RETURN m,t LIMIT 50")
    print("  MATCH path=(c:Class)-[*..3]->(d:Class) RETURN path LIMIT 20")


if __name__ == "__main__":
    main()
