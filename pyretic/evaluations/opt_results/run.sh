OLDIFS=$IFS 
IFS=','
for i in {1..20}
#for i in 1,1 1,2 1,3 2,2 2,3 2,4 2,5 3,3 3,4 3,5 4,4 4,5 5,5 
do 
#    set $i
    #sudo python eval_compilation.py -t congested_link -polargs n $1 m $2 -r ./opt_results/$1-$2
    cat $i-$i/regexes_to_dfa.profile 
#    sudo python eval_compilation.py -t path_packet_loss -polargs n $i -r ./opt_results/$i 
done
IFS=$OLDIFS